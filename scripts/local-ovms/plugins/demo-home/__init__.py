"""Two native Hermes tools backed by the independent local simulator."""
import json
from urllib.request import ProxyHandler, Request, build_opener


def request(path, args=None, port=8769):
    try:
        req = Request(f'http://127.0.0.1:{int(port)}{path}',
                      data=None if args is None else json.dumps(args).encode(),
                      headers={'Content-Type': 'application/json'})
        # Never route this local simulated home through corporate proxies.
        with build_opener(ProxyHandler({})).open(req, timeout=3) as response:
            result = json.loads(response.read(32768))
        if result.get('simulated') is not True or result.get('success') is not True:
            raise ValueError('Invalid simulator response')
        return json.dumps(result, ensure_ascii=False)
    except Exception:
        return json.dumps({'success': False, 'simulated': True,
                           'error': 'Cannot confirm appliance state or changes. Local demo service unavailable or request rejected. Do not claim success; do not retry automatically.'})


def register(ctx):
    port = int(ctx.get_config('port', 8769))
    for name, description, parameters, handler in (
        ('demo_home_status', 'Read all simulated home appliances and their current on/off states. Never assume states from memory.',
         {'type': 'object', 'properties': {}, 'additionalProperties': False},
         lambda args, **kw: request('/devices', port=port)),
        ('demo_home_set', 'Set explicitly authorized simulated appliances on/off. Use exact device IDs from a fresh query. Returns verified complete state; no real IoT is controlled.',
         {'type': 'object', 'properties': {
             'device_ids': {'type': 'array', 'items': {'type': 'string'}, 'minItems': 1, 'maxItems': 6},
             'state': {'type': 'string', 'enum': ['on', 'off']}},
          'required': ['device_ids', 'state'], 'additionalProperties': False},
         lambda args, **kw: request('/devices/set', args, port)),
    ):
        ctx.register_tool(name=name, toolset='demo_home',
                          schema={'name': name, 'description': description, 'parameters': parameters},
                          handler=handler, emoji='🏠')
    ctx.register_system_prompt_section(
        'demo-home.skill',
        'For household appliance status/control only, load the demo-home-assistant skill. '
        'Use the local demo_home tools as the source of truth; never invent device states. '
        'This simulated-home capability does not change how you handle other topics.',
        max_chars=400)
