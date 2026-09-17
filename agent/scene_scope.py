"""Opt-in CLI scene capability scope, independent of model intent parsing.

No process-global policy and no change to unscoped IM/native sessions. One
snapshot belongs to an agent generation; retries and worker threads inherit it.
This is a tool boundary, not a sandbox for arbitrary terminal code.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
from threading import Event

current_scope = ContextVar('scene_scope', default=None)


@dataclass(frozen=True)
class SceneScope:
    session_id: str
    generation: int
    scene: str | None
    skills: frozenset
    tools: frozenset
    active_skills: frozenset
    active_tools: frozenset
    revoked: Event = field(default_factory=Event)

    @property
    def cache_key(self):
        return (self.skills, self.tools, self.active_skills, self.active_tools,
                self.revoked.is_set())

    def allows_skill(self, name):
        parts = set(str(name).replace('\\', '/').split('/')) | {str(name)}
        restricted = parts & self.skills
        return not self.revoked.is_set() and restricted <= self.active_skills

    def allows_tool(self, name):
        return not self.revoked.is_set() and (name not in self.tools or name in self.active_tools)


@contextmanager
def bind(scope):
    token = current_scope.set(scope)
    try:
        yield
    finally:
        current_scope.reset(token)


def for_agent(fn):
    @wraps(fn)
    def wrapped(agent, *args, **kwargs):
        with bind(getattr(agent, '_scene_scope', None)):
            return fn(agent, *args, **kwargs)
    return wrapped


def for_cli_init(fn):
    @wraps(fn)
    def wrapped(cli, *args, **kwargs):
        controller = getattr(cli, '_scene_controller', None)
        scope = controller.scope() if controller is not None else None
        with bind(scope):
            return fn(cli, *args, **kwargs)
    return wrapped


def blocked_skills():
    scope = current_scope.get()
    return (scope.skills - scope.active_skills) if scope else frozenset()


def check_call(name, args=None):
    scope = current_scope.get()
    if scope is None:
        return None
    if not scope.allows_tool(name):
        return 'Tool unavailable in this scene generation.'
    args = args if isinstance(args, dict) else {}
    if name == 'skill_view' and not scope.allows_skill(args.get('name', '')):
        return 'Skill unavailable in this scene generation.'
    if name == 'tool_call':
        target = args.get('name', '')
        if not scope.allows_tool(target):
            return 'Tool unavailable in this scene generation.'
    return None
