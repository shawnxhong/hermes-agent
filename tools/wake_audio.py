"""PipeWire reference selection and one-shot AEC fallback for the wake sidecar."""
import json
import os
import re
import subprocess
import time


def snapshot():
    return json.loads(subprocess.check_output(['pw-dump'], text=True, timeout=5))


def props(node):
    return node.get('info', {}).get('props', {})


def resolve_reference(reference, graph, devices):
    sinks = [node for node in graph if props(node).get('media.class') == 'Audio/Sink']
    if reference == 'default':
        names = [item['value']['name'] for node in graph
                 if node.get('props', {}).get('metadata.name') == 'default'
                 for item in node.get('metadata', []) if item.get('key') == 'default.audio.sink']
        if len(names) != 1:
            raise ValueError('System default playback endpoint unavailable')
        reference = names[0]
    numeric = re.fullmatch(r'PLAYBACK_DEVICE:(\d+)', reference)
    if numeric:
        selected = [d for d in devices if d[0] == int(numeric[1])]
        if len(selected) != 1:
            raise ValueError('AEC reference index does not exist')
        matches = [node for node in sinks if
                   props(node).get('node.description', props(node).get('node.name')) == selected[0][1]]
    else:
        matches = [node for node in sinks if props(node).get('node.name') == reference]
    if len(matches) != 1:
        raise ValueError('AEC reference missing or ambiguous')
    node = matches[0]
    description = props(node).get('node.description', props(node).get('node.name'))
    # SDK exposes description + enumeration index, not node.name. Never guess
    # when descriptions collide, and never trust its is_default/index-zero flag.
    if sum(props(n).get('node.description', props(n).get('node.name')) == description for n in sinks) != 1:
        raise ValueError('AEC device descriptions are ambiguous')
    mapped = [d for d in devices if d[1] == description]
    if len(mapped) != 1:
        raise ValueError('Cannot uniquely map PipeWire endpoint to SDK device')
    return 'PLAYBACK_DEVICE:' + str(mapped[0][0]), props(node)['node.name']


def reference_connected(before, graph, target):
    old_ids = {n['id'] for n in before}
    fresh = [n for n in graph if n['id'] not in old_ids]
    sources = [n for n in fresh if props(n).get('node.name', '').startswith(f'aisp-aec-{os.getpid()}-')]
    if not sources:
        return False
    clients = {props(n).get('client.id') for n in sources} - {None}
    references = {n['id'] for n in fresh if props(n).get('client.id') in clients
                  and props(n).get('target.object') == target}
    target_ids = {n['id'] for n in graph if props(n).get('node.name') == target}
    return any(n.get('type') == 'PipeWire:Interface:Link'
               and n.get('info', {}).get('output-node-id') in target_ids
               and n.get('info', {}).get('input-node-id') in references for n in graph)


def start_capture_with_aec(native, source, audio, event, *, read_graph=snapshot):
    requested = audio['aec']
    if requested == 'none':
        native.start_capture(source, 'none', '')
        event(event='audio_capture_ready', requested_aec=requested, effective_aec='none')
        return 'none'
    try:
        before = read_graph()
        uri, target = resolve_reference(audio['aec_reference'], before, native.playback_devices())
        event(event='aec_reference_selected', node=target, sdk_device=uri)
        native.start_capture(source, 'os', uri)
        deadline = time.monotonic() + 4
        while not reference_connected(before, read_graph(), target):
            if not native.capture_alive() or time.monotonic() >= deadline:
                raise RuntimeError('AEC reference connection could not be confirmed')
            time.sleep(.1)
        event(event='audio_capture_ready', requested_aec=requested, effective_aec='os', reference=target)
        return 'os'
    except Exception as exc:
        # Destroy even a partially opened session before opening the microphone
        # again. A failed plain capture is a real startup error, not another retry.
        native.stop_capture()
        event(event='aec_fallback', requested_aec=requested, effective_aec='none',
              reason=str(exc))
        native.start_capture(source, 'none', '')
        event(event='audio_capture_ready', requested_aec=requested, effective_aec='none')
        return 'none'
