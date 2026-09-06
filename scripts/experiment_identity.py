"""Reject retrieval ablations that silently change the ingested experiment."""
import copy

# These fields affect retrieval or the local listener, not committed memories.
RETRIEVAL_FIELDS = {
    'host', 'port', 'rawFallback', 'rerank', 'coveragePacking', 'candidateLimit',
    'rerankCandidates', 'retrieval', 'eventView', 'maxEvidence', 'tokenBudget',
    'searchTimeout', 'ingestion_origin',
}


def ingestion_configuration(config):
    result = copy.deepcopy(config)
    for field in RETRIEVAL_FIELDS:
        result.pop(field, None)
    if isinstance(result.get('experimental'), dict):
        result['experimental'].pop('multiHop', None)
    return result


def validate_reuse(prior, current_config, dataset_sha256, source_identity):
    if prior.get('status') != 'finished':
        raise ValueError('Original experiment has not finished')
    if prior.get('dataset_sha256') != dataset_sha256:
        raise ValueError('Cannot reuse ingestion with a different dataset')
    for name in ('service', 'eval'):
        identity = source_identity[name]
        if not identity.get('commit') or prior.get(name + '_commit') != identity['commit']:
            raise ValueError('Cannot reuse ingestion after changing ' + name + ' commit')
        old_state = prior.get('source_state', {}).get(name, {})
        if old_state.get('dirty') is not False or identity.get('dirty') is not False:
            raise ValueError('Paired ingestion requires clean recorded and current ' + name + ' source')
    old_config = prior.get('service_configuration')
    if not isinstance(old_config, dict):
        raise ValueError('Original ingestion configuration is missing')
    old = ingestion_configuration(old_config)
    new = ingestion_configuration(current_config)
    if old != new:
        changed = sorted(k for k in old.keys() | new.keys() if old.get(k) != new.get(k))
        raise ValueError('Ingestion configuration changed: ' + ', '.join(changed))
