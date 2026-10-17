// Stopping geometry work from another thread: a build that is no longer needed
// (the design changed meanwhile) stops soon, inside long booleans too.
#pragma once

#include <atomic>
#include <stdexcept>

namespace mgeom {

// Work stopped because it was asked to (see Interruptible).
class Interrupted : public std::runtime_error {
public:
    Interrupted() : std::runtime_error("interrupted: no longer needed") {}
};

// While one lives, the geometry work of its thread stops soon after ``flag`` is
// set, throwing Interrupted: between operations, and inside Open CASCADE's
// booleans (on whichever threads they run). Scopes nest; null: not stoppable.
class Interruptible {
public:
    explicit Interruptible(const std::atomic<bool>* flag);
    ~Interruptible();
    Interruptible(const Interruptible&) = delete;
    Interruptible& operator=(const Interruptible&) = delete;

private:
    const std::atomic<bool>* previous_;
};

// The flag of this thread's innermost Interruptible (null when there is none).
const std::atomic<bool>* interrupt_flag();

// Whether this thread's work has been asked to stop.
bool interrupted();

// Throws Interrupted when this thread's work has been asked to stop.
void check_interrupted();

}  // namespace mgeom
