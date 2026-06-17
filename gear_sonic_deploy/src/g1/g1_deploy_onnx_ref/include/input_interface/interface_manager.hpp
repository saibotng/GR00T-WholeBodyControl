/**
 * @file interface_manager.hpp
 * @brief Multiplexer that owns several concrete InputInterface instances and
 *        delegates all calls to the currently-active one.
 *
 * InterfaceManager is itself an InputInterface so it can be used transparently
 * by the main control loop.  It creates and owns:
 *   - SimpleKeyboard   (index 0, default)
 *   - Gamepad          (index 1)
 *   - ZMQEndpointInterface (index 2)
 *   - ROS2InputHandler (index 3, only when compiled with HAS_ROS2)
 *   - ZMQManager       (last index; pico-teleop planner / streamed-motion mux)
 *
 * Switching is done via **keyboard shortcuts** typed into the terminal:
 *   '!' → Keyboard  |  '@' → Gamepad  |  '#' → ZMQ  |  '$' → ROS2
 *   '%' → ZMQManager (its own PLANNER/STREAMED_MOTION switch stays network-driven)
 *
 * The manager also handles several **global** controls that work regardless of
 * which interface is active:
 *   - 'O'/'o'  → emergency stop
 *   - g/G, h/H → left-hand compliance ±0.1
 *   - b/B, v/V → right-hand compliance ±0.1
 *   - x/X, c/C → hand max-close ratio ±0.1
 *
 * When switching interfaces, a **safety reset** is triggered on *all* managed
 * interfaces to prevent stale planner / streaming state from carrying over.
 */

#ifndef INTERFACE_MANAGER_HPP
#define INTERFACE_MANAGER_HPP

#include <memory>
#include <vector>
#include <iostream>
#include <cstring>
#include <cstdlib>

#include "input_interface.hpp"
#include "keyboard_handler.hpp"
#include "gamepad.hpp"
#include "zmq_endpoint_interface.hpp"
#include "zmq_manager.hpp"

#if HAS_ROS2
#include "ros2_input_handler.hpp"
#endif

/**
 * @class InterfaceManager
 * @brief Composite InputInterface that delegates to one of several concrete
 *        implementations, switchable at run-time via keyboard shortcuts.
 *
 * All getter methods (HasVR3PointControl, GetHandPose, etc.) are forwarded to
 * the currently-active delegate so the control loop always sees live values.
 */
class InterfaceManager : public InputInterface {
  public:
    /// Identifies which concrete interface is currently active.
    enum class ManagedType {
      KEYBOARD = 0,     ///< SimpleKeyboard (stdin)
      GAMEPAD = 1,      ///< Unitree wireless gamepad
      ZMQ = 2,          ///< ZMQ packed-message streaming
      ROS2 = 3,         ///< ROS 2 teleop (requires HAS_ROS2)
      ZMQ_MANAGER = 4   ///< ZMQManager: pico-teleop planner / streamed-motion mux
    };

    /**
     * @brief Construct the manager, creating all sub-interfaces.
     * @param zmq_host     ZMQ server hostname (passed to ZMQEndpointInterface).
     * @param zmq_port     ZMQ server port.
     * @param zmq_topic    ZMQ subscription topic.
     * @param zmq_conflate Whether to enable ZMQ conflate (latest-only) mode.
     * @param zmq_verbose  Enable verbose ZMQ logging.
     */
    InterfaceManager(
      const std::string& zmq_host,
      int zmq_port,
      const std::string& zmq_topic,
      bool zmq_conflate,
      bool zmq_verbose,
      const std::string& zmq_command_topic = "command",
      const std::string& zmq_planner_topic = "planner"
    ) : InputInterface(), zmq_host_(zmq_host), zmq_port_(zmq_port), zmq_topic_(zmq_topic),
        zmq_conflate_(zmq_conflate), zmq_verbose_(zmq_verbose),
        zmq_command_topic_(zmq_command_topic), zmq_planner_topic_(zmq_planner_topic) {
      type_ = InputType::UNKNOWN;
      buildInterfaces();
      setActiveIndex(0); // default to keyboard (index 0)
    }

    void update() override {
      // Reset per-frame flags
      emergency_stop_ = false;
      report_temperature_flag_ = false;
      
      // Read stdin using shared buffering mechanism, check for manager keys
      char ch;
      while (ReadStdinChar(ch)) {
        bool is_manager_key = false;
        switch (ch) {
          case '!': 
            SetActiveInterface(ManagedType::KEYBOARD); 
            is_manager_key = true;
            break;
          case '@': 
            SetActiveInterface(ManagedType::GAMEPAD); 
            is_manager_key = true;
            break;
          case '#': 
            SetActiveInterface(ManagedType::ZMQ); 
            is_manager_key = true;
            break;
          case '$':
            SetActiveInterface(ManagedType::ROS2);
            is_manager_key = true;
            break;
          case '%':
            SetActiveInterface(ManagedType::ZMQ_MANAGER);
            is_manager_key = true;
            break;
          case 'o':
          case 'O':
            // Global emergency stop - works for all interfaces (especially gamepad)
            emergency_stop_ = true;
            is_manager_key = true;
            std::cout << "[InterfaceManager] EMERGENCY STOP triggered (O/o key pressed)" << std::endl;
            break;
          // Global compliance controls - work across ALL interfaces (keyboard, planner, ROS2, ZMQ)
          case 'g':
          case 'G':
            // Increase left hand compliance by 0.1
            AdjustLeftHandCompliance(0.1);
            is_manager_key = true;
            break;
          case 'h':
          case 'H':
            // Decrease left hand compliance by 0.1
            AdjustLeftHandCompliance(-0.1);
            is_manager_key = true;
            break;
          case 'b':
          case 'B':
            // Increase right hand compliance by 0.1
            AdjustRightHandCompliance(0.1);
            is_manager_key = true;
            break;
          case 'v':
          case 'V':
            // Decrease right hand compliance by 0.1
            AdjustRightHandCompliance(-0.1);
            is_manager_key = true;
            break;
          // Global hand max close ratio controls - work across ALL interfaces (x/c keys)
          case 'x':
          case 'X':
            // Increase max close ratio by 0.1 (allow hands to close more)
            AdjustMaxCloseRatio(0.1);
            is_manager_key = true;
            break;
          case 'c':
          case 'C':
            // Decrease max close ratio by 0.1 (keep hands more open)
            AdjustMaxCloseRatio(-0.1);
            is_manager_key = true;
            break;
          case 'f':
          case 'F':
            // Global temperature report
            report_temperature_flag_ = true;
            is_manager_key = true;
            break;
        }
        
        // Buffer non-manager keys for the active interface to read
        // Note: 'O'/'o' is NOT passed through since it's handled globally
        if (!is_manager_key) {
          current_->PushStdinChar(ch);
        }
      }

      // Run the actual active interface update (it will read buffered keys)
      current_->update();

      // Note: We don't cache data here anymore - just forward calls to active interface
      // This ensures we always get the most current values
    }

    void handle_input(MotionDataReader& motion_reader,
                      std::shared_ptr<const MotionSequence>& current_motion,
                      int& current_frame,
                      OperatorState& operator_state,
                      bool& reinitialize_heading,
                      DataBuffer<HeadingState>& heading_state_buffer,
                      bool has_planner,
                      PlannerState& planner_state,
                      DataBuffer<MovementState>& movement_state_buffer,
                      std::mutex& current_motion_mutex,
                      bool& report_temperature) override {
      
      // Handle global emergency stop (works for all interfaces, especially gamepad)
      if (emergency_stop_) {
        operator_state.stop = true;
      }

      // Handle global temperature report (F key)
      if (report_temperature_flag_) {
        report_temperature = true;
        report_temperature_flag_ = false;
      }

      // Check if ROS2 is active but planner is not available - switch to keyboard
      if (active_ == ManagedType::ROS2 && !has_planner) {
        std::cout << "[InterfaceManager] ROS2 requires planner but planner not loaded. Switching to KEYBOARD" << std::endl;
        SetActiveInterface(ManagedType::KEYBOARD);
      }

      // ZMQManager boots in PLANNER mode, which needs a planner. Without one,
      // fall back to keyboard rather than feeding the control loop dead planner
      // commands. (Pico teleop is always launched with --planner-file.)
      if (active_ == ManagedType::ZMQ_MANAGER && !has_planner) {
        std::cout << "[InterfaceManager] ZMQ_MANAGER requires planner but planner not loaded. Switching to KEYBOARD" << std::endl;
        SetActiveInterface(ManagedType::KEYBOARD);
      }

      // Delegate to the active interface
      current_->handle_input(motion_reader, current_motion, current_frame, operator_state,
                             reinitialize_heading, heading_state_buffer, has_planner, planner_state,
                             movement_state_buffer, current_motion_mutex, report_temperature);
    }
    
    // Override all getters to forward directly to the active interface
    // This makes the manager a transparent proxy that always returns live values
    
    
    bool HasVR3PointControl() const override {
      if (current_) {
        return current_->HasVR3PointControl();
      }
      return has_vr_3point_control_;
    }
    
    bool HasHandJoints() const override {
      if (current_) {
        return current_->HasHandJoints();
      }
      return has_hand_joints_;
    }
    
    bool HasExternalTokenState() const override {
      if (current_) {
        return current_->HasExternalTokenState();
      }
      return has_external_token_state_;
    }
    
    std::pair<bool, std::array<double, 9>> GetVR3PointPosition() const override {
      if (current_) {
        return current_->GetVR3PointPosition();
      }
      return InputInterface::GetVR3PointPosition();  // Fallback to base class
    }
    
    std::pair<bool, std::array<double, 12>> GetVR3PointOrientation() const override {
      if (current_) {
        return current_->GetVR3PointOrientation();
      }
      return InputInterface::GetVR3PointOrientation();  // Fallback to base class
    }
    
    std::pair<bool, std::array<double, 7>> GetHandPose(bool is_left) const override {
      if (current_) {
        return current_->GetHandPose(is_left);
      }
      return InputInterface::GetHandPose(is_left);  // Fallback to base class
    }
    
    std::pair<bool, std::vector<double>> GetExternalTokenState() const override {
      if (current_) {
        return current_->GetExternalTokenState();
      }
      return InputInterface::GetExternalTokenState();  // Fallback to base class
    }

    /// Forward raw wireless-remote byte buffer to the internal Gamepad instance.
    /// Called by the Unitree SDK callback whenever new joystick data arrives.
    void UpdateGamepadRemoteData(const uint8_t* buff, size_t size) {
      if (!gamepad_ || buff == nullptr || size == 0) { return; }
      size_t copy_size = std::min<size_t>(size, sizeof(gamepad_->gamepad_data.buff));
      std::memcpy(gamepad_->gamepad_data.buff, buff, copy_size);
    }

    /// Programmatically switch to a specific interface type.
    /// Triggers safety reset on all interfaces and prints a log message.
    void SetActiveInterface(ManagedType t) {
      for (size_t i = 0; i < order_.size(); ++i) {
        if (order_[i] == t) {
          setActiveIndex(static_cast<int>(i));
          return;
        }
      }
    }

    ManagedType GetActiveInterface() const { return active_; }

  private:
    /// Instantiate all concrete interfaces and register them in order_.
    void buildInterfaces() {
      keyboard_ = std::make_unique<SimpleKeyboard>();
      order_.push_back(ManagedType::KEYBOARD);

      gamepad_ = std::make_unique<unitree::common::Gamepad>();
      order_.push_back(ManagedType::GAMEPAD);

      zmq_ = std::make_unique<ZMQEndpointInterface>(
        zmq_host_, zmq_port_, zmq_topic_, zmq_conflate_, zmq_verbose_
      );
      order_.push_back(ManagedType::ZMQ);

#if HAS_ROS2
      ros2_ = std::make_unique<ROS2InputHandler>(true, "g1_deploy_ros2_handler");
      order_.push_back(ManagedType::ROS2);
#endif

      // ZMQManager owns its own pose/command/planner subscribers; they run for
      // the whole session even while this delegate is inactive. Activation does
      // a safety reset so queued commands don't leak across switches.
      zmq_manager_ = std::make_unique<ZMQManager>(
        zmq_host_, zmq_port_, zmq_topic_, zmq_command_topic_, zmq_planner_topic_,
        zmq_conflate_, zmq_verbose_
      );
      order_.push_back(ManagedType::ZMQ_MANAGER);
    }

    /// Set the active interface by numeric index (wraps around).
    /// Triggers safety reset on ALL interfaces to prevent stale state.
    void setActiveIndex(int idx) {
      if (order_.empty()) { return; }
      if (idx < 0) { idx = static_cast<int>(order_.size()) - 1; }
      if (idx >= static_cast<int>(order_.size())) { idx = 0; }
      
      // Trigger safety reset on ALL interfaces when switching
      keyboard_->TriggerSafetyReset();
      gamepad_->TriggerSafetyReset();
      zmq_->TriggerSafetyReset();
#if HAS_ROS2
      if (ros2_) ros2_->TriggerSafetyReset();
#endif
      if (zmq_manager_) zmq_manager_->TriggerSafetyReset();

      active_index_ = idx;
      active_ = order_[static_cast<size_t>(active_index_)];

      switch (active_) {
        case ManagedType::KEYBOARD:
          current_ = keyboard_.get();
          type_ = InputType::KEYBOARD;
          std::cout << "[InterfaceManager] Switched to: KEYBOARD (safety reset triggered)" << std::endl;
          break;
        case ManagedType::GAMEPAD:
          current_ = gamepad_.get();
          type_ = InputType::GAMEPAD;
          std::cout << "[InterfaceManager] Switched to: GAMEPAD (safety reset triggered)" << std::endl;
          break;
        case ManagedType::ZMQ:
          current_ = zmq_.get();
          type_ = InputType::NETWORK;
          std::cout << "[InterfaceManager] Switched to: ZMQ (safety reset triggered)" << std::endl;
          break;
        case ManagedType::ROS2:
#if HAS_ROS2
          current_ = ros2_.get();
          type_ = InputType::ROS2;
          std::cout << "[InterfaceManager] Switched to: ROS2 (safety reset triggered)" << std::endl;
          break;
#else
          // Should never happen when ROS2 disabled; fall back to keyboard
          current_ = keyboard_.get();
          type_ = InputType::KEYBOARD;
          active_ = ManagedType::KEYBOARD;
          std::cout << "[InterfaceManager] ROS2 not available. Falling back to KEYBOARD (safety reset triggered)" << std::endl;
          break;
#endif
        case ManagedType::ZMQ_MANAGER:
          current_ = zmq_manager_.get();
          type_ = InputType::NETWORK;
          std::cout << "[InterfaceManager] Switched to: ZMQ_MANAGER (safety reset triggered)" << std::endl;
          break;
      }
    }

    void nextInterface() { setActiveIndex(active_index_ + 1); }  ///< Cycle forward.
    void prevInterface() { setActiveIndex(active_index_ - 1); }  ///< Cycle backward.

  private:
    // ------------------------------------------------------------------
    // Owned concrete delegates (kept alive to preserve state across switches)
    // ------------------------------------------------------------------
    std::unique_ptr<SimpleKeyboard> keyboard_;              ///< Keyboard handler.
    std::unique_ptr<unitree::common::Gamepad> gamepad_;     ///< Gamepad handler.
    std::unique_ptr<ZMQEndpointInterface> zmq_;             ///< ZMQ streaming handler.
#if HAS_ROS2
    std::unique_ptr<ROS2InputHandler> ros2_;                ///< ROS 2 teleop handler.
#endif
    std::unique_ptr<ZMQManager> zmq_manager_;               ///< Pico-teleop planner/streamed-motion mux.

    InputInterface* current_ = nullptr;  ///< Non-owning pointer to the active delegate.

    // ------------------------------------------------------------------
    // Active-selection bookkeeping
    // ------------------------------------------------------------------
    std::vector<ManagedType> order_;         ///< Insertion-order of managed types.
    int active_index_ = 0;                   ///< Index into order_.
    ManagedType active_ = ManagedType::KEYBOARD;  ///< Currently-active type tag.

    // ZMQ configuration (stored for deferred construction)
    std::string zmq_host_;
    int zmq_port_;
    std::string zmq_topic_;
    bool zmq_conflate_ = false;
    bool zmq_verbose_ = false;
    std::string zmq_command_topic_ = "command";  ///< ZMQManager command topic (start/stop/mode).
    std::string zmq_planner_topic_ = "planner";   ///< ZMQManager planner (movement) topic.
    
    /// Global emergency-stop flag, set by 'O'/'o' key.
    /// Applies to ALL interfaces (especially useful when gamepad is active
    /// and has no physical stop button readily accessible).
    bool emergency_stop_ = false;
    bool report_temperature_flag_ = false;
};

#endif // INTERFACE_MANAGER_HPP


