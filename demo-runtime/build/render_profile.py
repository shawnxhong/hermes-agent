#!/usr/bin/env python3
"""Render a standalone, private profile config; never read a live runtime.

Output is JSON (a YAML subset accepted by Hermes), despite its config.yaml name.
Only explicitly declared binding leaves are substitutable; no arbitrary overlay.
"""
import argparse
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROFILES = ('state', 'gateway-state')


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def read_json(path):
    return json.loads(Path(path).read_text(), object_pairs_hook=unique_object)


def render(profile, bindings, root=ROOT):
    if profile not in PROFILES:
        raise ValueError('Unknown profile')
    template = read_json(root / 'profiles' / f'{profile}.template.json')
    schema = read_json(root / 'profiles' / f'{profile}.bindings.schema.json')
    expected = set(schema['required'])
    if not isinstance(bindings, dict) or set(bindings) != expected:
        raise ValueError('Binding keys must exactly match this profile schema')
    if any(not isinstance(v, str) or not v.strip() or v == 'REPLACE_ME'
           for v in bindings.values()):
        raise ValueError('Bindings require nonempty strings; replace all example values')
    used = set()

    def replace(value):
        if isinstance(value, dict):
            if '$binding' in value:
                if set(value) != {'$binding'} or value['$binding'] not in expected:
                    raise ValueError('Invalid template binding')
                used.add(value['$binding'])
                return bindings[value['$binding']]
            return {k: replace(v) for k, v in value.items()}
        if isinstance(value, list):
            return [replace(v) for v in value]
        return value

    result = replace(template)
    if used != expected:
        raise ValueError('Schema and template bindings disagree')
    return result


def write_new(path, config):
    # Exclusive creation rejects existing files and symlinks, including dangling
    # symlinks. Serialize before opening so serialization errors leave no file.
    payload = (json.dumps(config, indent=2, ensure_ascii=True) + '\n').encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        Path(path).unlink(missing_ok=True)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', required=True, choices=PROFILES)
    parser.add_argument('--bindings', required=True, type=Path)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument('--output', type=Path)
    target.add_argument('--check', action='store_true')
    args = parser.parse_args()
    try:
        config = render(args.profile, read_json(args.bindings))
        if args.output:
            write_new(args.output, config)
    except (ValueError, OSError, KeyError, TypeError):
        # Do not echo private values, JSON parser source excerpts or config.
        parser.exit(2, 'Profile generation failed: check schema, private input and output availability.\n')
    print('Profile validated.' if args.check else 'Profile written with private permissions.')


if __name__ == '__main__':
    main()
