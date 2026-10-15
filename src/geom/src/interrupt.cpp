#include "mgeom/interrupt.hpp"

namespace mgeom {

namespace {
thread_local const std::atomic<bool>* current = nullptr;
}

Interruptible::Interruptible(const std::atomic<bool>* flag) : previous_(current) { current = flag; }

Interruptible::~Interruptible() { current = previous_; }

const std::atomic<bool>* interrupt_flag() { return current; }

bool interrupted() { return current && current->load(std::memory_order_relaxed); }

void check_interrupted() {
    if (interrupted()) throw Interrupted();
}

}  // namespace mgeom
