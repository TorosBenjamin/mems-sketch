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
        {"arc", render_arc},
        {"path", render_path},
        {"guide", render_guide},
        {"offset", render_offset},
        {"fillet", render_fillet},
        {"layer_map", render_layer_map},
    };
    const auto found = kinds.find(kind);
    return found == kinds.end() ? nullptr : found->second;
}

}  // namespace mems
