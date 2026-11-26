"""Explicit recovery ceilings for new approvals; legacy approvals keep their bounds."""
from copy import deepcopy

KEY = 'whole_project_recovery_limits'
EXPANDED = {'policy': 'explicit-recovery-limits-v1', 'output_corrections': 16,
            'test_defect_repairs': 16, 'coordinated_validations': 16}


def validate(value):
    if (not isinstance(value, dict) or value != EXPANDED
            or any(type(v) is not int for k, v in value.items() if k != 'policy')):
        raise ValueError('Recovery limits must match the explicitly supported 16/16/16 grant')
    return deepcopy(value)


def approved(bundle):
    if KEY not in bundle:
        return None
    value = validate(bundle[KEY])
    from .stage_recovery import dynamic_enabled
    if not dynamic_enabled(bundle):
        raise ValueError('Expanded recovery limits require approved dynamic stage recovery')
    epoch = bundle.get('whole_project_stage_continuation', {})
    if epoch:
        from .smoke_continuation import POLICY as SMOKE_POLICY
        if epoch.get('policy') == SMOKE_POLICY:
            return value  # Carries the exact explicit grant and every spent counter.
        from .checkpoint_repair_continuation import POLICY, approved_budgets
        if epoch.get('policy') != POLICY:
            raise ValueError('Explicit recovery limits cannot override a different continuation grant')
        inherited = approved_budgets(epoch['fresh_budgets'])
        if any(inherited.get(k) != value[k] for k in ('output_corrections', 'test_defect_repairs', 'coordinated_validations')):
            raise ValueError('Recovery limits differ from the approved continuation allowance')
    return value
