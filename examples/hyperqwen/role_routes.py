"""Validate explicit OpenCode role routes before approval or dispatch."""
import native_adapter as native


def validate_opencode_routes(profile):
    workers = profile.get('whole_project_workers', {})
    backend = profile.get('backend', {})
    if (set(workers) != set(native.ROLES) or backend.get('agent_transport') != 'opencode-cli'
            or backend.get('auto_compaction') is not True):
        raise ValueError('Every workflow role must use compacting OpenCode')
    routes = {**workers, 'reviewer': backend.get('review_worker', {})}
    if (set(profile.get('roles', {})) != set(routes)
            or any(worker.get('name') != 'opencode' or worker.get('role') != role
                or worker.get('auto_compaction') is not True or worker.get('structured_compaction_steps') != 9
                or worker.get('model') != profile['roles'][role] for role, worker in routes.items())
            or len({worker.get('executable_sha256') for worker in routes.values()}) != 1
            or any(item.get('material_policy') != 'maximum-public-role-framing-plus-schema'
                for item in [*profile['whole_project_prompt_tokenizers'].values(), backend.get('tokenizer', {})])):
        raise ValueError('Workflow role routes differ from their frozen OpenCode/compaction identities')
    return routes

