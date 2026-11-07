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
        return result

    return convert(original)
