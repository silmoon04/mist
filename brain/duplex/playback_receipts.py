"""Validate browser-reported caption display against audio sent on this socket.

These receipts describe the browser's playback clock and rendered caption. They
cannot prove speaker output, audibility, or that a person heard the words.
"""

from collections import OrderedDict
import re


class PlaybackReceipts:
    def __init__(self):
        self.epoch = 0
        self._sent = OrderedDict()
        self._accepted = {}
        self.latest = None

    def observe_audio(self, event):
        epoch, sequence = event.get('epoch'), event.get('seq')
        if type(epoch) is not int or type(sequence) is not int or epoch < self.epoch:
            return
        if epoch > self.epoch:
            self.reset(epoch)
        cues = event.get('caption_cues')
        if not isinstance(cues, list):
            return
        key = (epoch, sequence)
        allowed = self._sent.setdefault(key, set())
        source = event.get('caption_source')
        if not isinstance(source, str) or source == 'unavailable':
            return
        for cue in cues:
            if isinstance(cue, dict):
                value = cue.get('text')
                if isinstance(value, str) and 0 < len(value) <= 8000:
                    allowed.add((re.sub(r'\s+', ' ', value).lstrip(), source))
        self._sent.move_to_end(key)
        while len(self._sent) > 8:
            old, _ = self._sent.popitem(last=False)
            self._accepted.pop(old, None)

    def reset(self, epoch):
        if type(epoch) is int and epoch > self.epoch:
            self.epoch = epoch
            self._sent.clear()
            self._accepted.clear()

    def accept(self, event):
        """Return an accepted, bounded receipt, or None for invalid/duplicate input."""
        if not isinstance(event, dict) or event.get('type') != 'caption_progress':
            return None
        epoch, sequence = event.get('epoch'), event.get('sequence')
        text, source = event.get('text'), event.get('source')
        if (type(epoch) is not int or type(sequence) is not int or
                epoch != self.epoch or not isinstance(text, str) or not text or
                len(text) > 8000 or not isinstance(source, str)):
            return None
        key = (epoch, sequence)
        if (self.latest and self.latest['epoch'] == epoch and
                sequence < self.latest['sequence']):
            return None
        if (text, source) not in self._sent.get(key, ()):
            return None
        previous = self._accepted.get(key, '')
        if len(text) <= len(previous) or not text.startswith(previous):
            return None
        self._accepted[key] = text
        receipt = {'type': 'playback_receipt', 'basis': 'client_reported_caption_display',
                   'epoch': epoch, 'sequence': sequence, 'text': text,
                   'caption_source': source, 'complete': False}
        client_ms = event.get('client_ms')
        if type(client_ms) in (int, float) and 0 <= client_ms < 1e12:
            receipt['client_ms'] = client_ms
        self.latest = receipt
        return receipt

    def context(self):
        receipt = self.latest
        if not receipt:
            return ''
        return ('Latest browser-reported displayed speech caption '
                f'(epoch {receipt["epoch"]}, sequence {receipt["sequence"]}; '
                'may be partial, audio hearing unverified): '
                + receipt['text'][-400:])
