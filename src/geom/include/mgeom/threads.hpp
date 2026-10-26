// How many threads the library's booleans may use (Open CASCADE runs them in
// parallel): the editor's performance setting (requirement QS-11).
#pragma once

namespace mgeom {

// Use up to ``count`` threads for one operation; 1 runs them on the calling
// thread only, 0 or less as many as the machine has (the default).
void set_threads(int count);

// The threads one operation may use (the machine's when not set).
int threads();

}  // namespace mgeom
