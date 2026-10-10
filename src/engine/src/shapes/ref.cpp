// A placed component (mems_sketch.core.shapes.kinds.ref): built with the
// parameter values the reference gives (expressions over the placing
// component's values), and placed whole: an instance of what was built,
// moved, rotated and mirrored.
#include <algorithm>

#include "kinds.hpp"
#include "mgeom/transform.hpp"

namespace mems {

Result render_ref(const Json& node, const Context& ctx) {
    const Project& project = ctx.builder.project();
    const std::string target = project.qualify(node.at("component").get<std::string>(), ctx.component);
    const ComponentDef* definition = project.definition(target);  // none: an imported cell
    // Values a placement may not set: the component's internal parameters.
    Values values;
    std::vector<std::string> hidden;
    const Json params = node.value("params", Json::object());  // a copy: kept for the loop
    for (const auto& [name, value] : params.items()) {
        if (definition)
            for (const auto& param : definition->parameters)
                if (param.name == name && param.internal) hidden.push_back(name);
        values.emplace_back(name, ctx.value(value));
    }
    if (!hidden.empty()) {
        std::sort(hidden.begin(), hidden.end());
        std::string listed;
        for (const auto& name : hidden) listed += (listed.empty() ? "'" : ", '") + name + "'";
        throw BuildError(listed + " of component '" + target + "' " + (hidden.size() == 1 ? "is" : "are") +
                         " internal: it cannot be set where the component is placed");
    }
    const mgeom::Transform placement{ctx.number(node, "x", 0.0), ctx.number(node, "y", 0.0),
                                     ctx.number(node, "rotation", 0.0), node.value("mirror_x", false), 1.0};
    OptionalLevel level;
    try {
        OptionalLevel spec;
        if (node.contains("level") && !node["level"].is_null()) spec = node["level"].get<std::string>();
        level = project.place(spec, definition ? definition->level : std::nullopt, ctx.builder.level());
    } catch (const ModelError& error) {
        const std::string name = node.contains("name") && node["name"].is_string() ? node["name"].get<std::string>()
                                                                                    : node["component"].get<std::string>();
        throw BuildError("'" + name + "': " + error.what());
    }
    auto built = ctx.builder.build_on(target, values, level);
    Result result;
    for (const auto& [name, point] : built->points) result.points.emplace(name, placement.apply(point));
    result.instances.push_back({std::move(built), placement});
    return result;
}

}  // namespace mems
