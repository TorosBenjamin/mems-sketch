#include "kinds.hpp"

#include <map>
#include <string>

namespace mems {

RenderKind find_kind(std::string_view kind) {
    static const std::map<std::string, RenderKind, std::less<>> kinds = {
        {"rect", render_rect},
        {"polygon", render_polygon},
        {"circle", render_circle},
        {"boolean", render_boolean},
        {"transform", render_transform},
        {"group", render_transform},  // the earlier name of transform
        {"ref", render_ref},
    };
    const auto found = kinds.find(kind);
    return found == kinds.end() ? nullptr : found->second;
}

}  // namespace mems
