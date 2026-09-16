"""Delay microphone monitoring, never inference, behind the turn-start ack."""
import logging
import threading
import time

log = logging.getLogger(__name__)


def arm_listener_after_ack(cli, barrier, text_queue, stop_event):
    from hermes_cli.voice_scenes import generation, switching
    session, version = cli.session_id, generation(cli)

    def current():
        return (cli.session_id == session and generation(cli) == version
                and not switching(cli) and not getattr(cli, '_should_exit', False)
                and cli._voice_mode and cli._voice_continuous
                and (stop_event is None or not stop_event.is_set())
                and (text_queue is None or getattr(cli, '_voice_turn_tts_queue', None) is text_queue))

    def monitor():
        if barrier is not None:
            deadline = time.monotonic() + 30
            while current():
                if barrier.wait(timeout=.05):
                    log.info('voice_latency stage=ack_complete session=%s', session)
                    break
                if time.monotonic() >= deadline:
                    log.warning('Voice ack timed out; microphone monitor remains off for this turn')
                    return
            else:
                return
        if current() and not any(getattr(cli, name, False) for name in (
            '_voice_clarify_preparing', '_voice_clarify_listening',
            '_voice_approval_preparing', '_voice_approval_listening',
        )):
            cli._voice_full_duplex_listener()

    worker = threading.Thread(target=monitor, daemon=True, name='voice-ack-monitor')
    worker.start()
    return worker
