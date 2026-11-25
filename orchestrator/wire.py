import copy

def wire_schema(model):
    """Inline Pydantic refs into the existing general API's JSON Schema subset."""
    original = model.model_json_schema()
    allowed = {
        "type",
        "enum",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "minItems",
        "maxItems",
        "minLength",
        "maxLength",
        "minimum",
        "maximum",
    }

    conditional_actions = getattr(model, 'conditional_actions', False) is True

    def convert(node):
        if "$ref" in node:
            return convert(original["$defs"][node["$ref"].rsplit("/", 1)[-1]])
        result = {k: copy.deepcopy(v) for k, v in node.items() if k in allowed}
        if "properties" in result:
            result["properties"] = {
                k: convert(v) for k, v in result["properties"].items()
            }
        if "items" in result:
            result["items"] = convert(result["items"])
        if conditional_actions and 'oneOf' in node:
            result['oneOf'] = [convert(branch) for branch in node['oneOf']]
        return result

    result = convert(original)
    if conditional_actions and 'oneOf' in result:
        # Some structured decoders select a branch without merging sibling
        # root constraints. Each branch must be a complete response schema.
        branches = result.pop('oneOf')
        complete = []
        for branch in branches:
            value = copy.deepcopy(result)
            value['properties'].update(branch.get('properties', {}))
            value['required'] = sorted(set(value.get('required', [])) | set(branch.get('required', [])))
            complete.append(value)
        return {'type': 'object', 'oneOf': complete}
    return result
