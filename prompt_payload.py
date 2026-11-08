"""Lossless model-wire serialization, separate from stored source and cache keys.

No text, timestamps, identifiers, event types or playback metadata are removed.
Uniform event records share one column header; irregular records remain objects.
"""
import copy
import json

CORE_COLUMNS = ('id', 'start', 'end', 'speaker', 'kind', 'text')
SOURCE_FORMAT = (' Source event tables: event_columns names the fields of each event_rows row, '
                 'in order. Each row is one complete original event; all times are numeric seconds. '
                 'Use the original IDs exactly; source time and playback time are distinct.')


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def pack_events(value):
    """Return a fresh value; pack only uniform, explicitly identifiable events."""
    if isinstance(value, list):
        if (value and all(isinstance(e, dict) and set(CORE_COLUMNS) <= e.keys() for e in value)
                and all(e.keys() == value[0].keys() for e in value)):
            columns = list(CORE_COLUMNS) + sorted(set(value[0]) - set(CORE_COLUMNS))
            table = dict(event_columns=columns,
                         event_rows=[[copy.deepcopy(e[k]) for k in columns] for e in value])
            # Tiny tables can cost more than objects. Never enlarge the payload.
            if len(_json(table)) < len(_json(value)):
                return table
        return [pack_events(item) for item in value]
    if isinstance(value, dict):
        return {key: pack_events(item) for key, item in value.items()}
    return value


def unpack_events(value):
    """Decode our wire form for offline fidelity checks and model test doubles."""
    if isinstance(value, dict):
        if set(value) == {'event_columns', 'event_rows'}:
            columns, rows = value['event_columns'], value['event_rows']
            if (isinstance(columns, list) and len(columns) == len(set(columns))
                    and set(CORE_COLUMNS) <= set(columns) and isinstance(rows, list)
                    and all(isinstance(row, list) and len(row) == len(columns) for row in rows)):
                return [dict(zip(columns, row)) for row in rows]
        return {key: unpack_events(item) for key, item in value.items()}
    if isinstance(value, list):
        return [unpack_events(item) for item in value]
    return value


def payload_json(value):
    return _json(pack_events(value))


def draft_context(draft):
    """Keep creative intent, source cut and useful critique; omit saved UI history."""
    result = {key: copy.deepcopy(draft[key]) for key in
              ('angle', 'distinction', 'segments', 'beats', 'question', 'reason', 'weakness')
              if key in draft}
    feedback = {key: copy.deepcopy(draft[key]) for key in ('narrative', 'review_notes', 'rubric')
                if draft.get(key)}
    failed = [c['name'] + ': ' + c['detail'] for c in draft.get('validation', {}).get('checks', [])
              if not c['passed']]
    if failed:
        feedback['failed_checks'] = failed
    if feedback:
        result['review_feedback'] = feedback
    return result
