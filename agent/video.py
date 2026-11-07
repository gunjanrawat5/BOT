"""Capture original gameplay, without rerunning or relabeling it as verification."""
import json
import logging
from pathlib import Path
from tempfile import TemporaryDirectory
import time


def attach_video(report_folder, video):
    folder = Path(report_folder)
    report_path = folder / 'bug.json'
    report = json.loads(report_path.read_text())
    try:
        video.save_as(folder / 'evidence.webm')
        if not (folder / 'evidence.webm').stat().st_size:
            raise ValueError('Empty video')
        report['video'] = {'file': 'evidence.webm', 'status': 'ready',
                           'kind': 'original_gameplay_recording', 'scope': 'full_agent_session', 'includes_planning_pauses': True}
        markdown = folder / 'bug.md'
        text = markdown.read_text()
        text = text.replace('## Screenshots', '## Gameplay recording\n\n[Watch the original recorded run](evidence.webm). '
                            'This is discovery footage, not an independent verification replay.\n\n## Screenshots', 1)
        markdown.write_text(text)
        logging.info('Gameplay recording saved: %s', folder / 'evidence.webm')
    except Exception as exc:
        (folder / 'evidence.webm').unlink(missing_ok=True)
        report['video'] = {'status': 'unavailable', 'kind': 'original_gameplay_recording',
                           'reason': f'Recording could not be finalized ({type(exc).__name__})'}
        logging.warning('Video could not be finalized (%s); screenshot evidence remains available.', type(exc).__name__)
    temporary = folder / 'bug.json.tmp'
    temporary.write_text(json.dumps(report, indent=2))
    temporary.replace(report_path)


class RunRecording:
    def __init__(self, browser, *, enabled=True):
        self.temp = TemporaryDirectory(prefix='spaceqa-video-') if enabled else None
        self.enabled = enabled
        self.report_folder = None
        options = {'viewport': {'width': 1000, 'height': 900}}
        if enabled:
            options.update(record_video_dir=self.temp.name,
                           record_video_size={'width': 1000, 'height': 900})
        try:
            self.context = browser.new_context(**options)
        except Exception as exc:
            if not enabled:
                raise
            logging.warning('Video recording unavailable (%s); continuing with screenshots.', type(exc).__name__)
            self.temp.cleanup()
            self.temp = None
            self.enabled = False
            self.context = browser.new_context(viewport=options['viewport'])
        self.page = self.context.new_page()
        self.started = time.monotonic()
        self.video = self.page.video if self.enabled else None

    def close(self):
        try:
            # Playwright finalizes the recording on context close.
            self.context.close()
            if self.report_folder is not None and self.video is not None:
                attach_video(self.report_folder, self.video)
        finally:
            if self.temp is not None:
                self.temp.cleanup()
