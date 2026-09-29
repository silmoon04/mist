"""Run one affect reader beside the speaker, without delaying speech or tools."""
import asyncio
import time


class AffectController:
    def __init__(self, reader, emit, apply):
        self.reader, self.emit, self.apply = reader, emit, apply
        self.task = None
        self.closed = False

    async def submit(self, snapshot, token):
        if self.closed:
            return
        if self.task is not None and not self.task.done():
            await self.emit({'type':'affect_decision','phase':'skipped_busy','revision_context':token})
            return
        self.task = asyncio.create_task(self.run(snapshot, token))

    async def run(self, snapshot, token):
        if self.closed:
            return
        started = time.monotonic()
        await self.emit({'type':'affect_decision','phase':'started','revision_context':token,
                         'model':self.reader.model,'input':snapshot})
        try:
            if self.closed:
                return
            decision, trace = await asyncio.to_thread(self.reader.decide_with_trace, snapshot)
            if self.closed:
                return
            outcome = 'rejected'
            if trace['status'] == 'accepted':
                outcome = await self.apply(decision, token)
            await self.emit({'type':'affect_decision','phase':'finished','revision_context':token,
                'decision':decision,'trace':trace,'outcome':outcome,
                'elapsed_ms':round((time.monotonic()-started)*1000,1)})
        except asyncio.CancelledError:
            raise
        except Exception as error:
            if not self.closed:
                await self.emit({'type':'affect_decision','phase':'failed','revision_context':token,
                                 'error_type':type(error).__name__})

    async def close(self):
        self.closed = True
        # The reader owns its bounded HTTP request and closes it in finally.
        # Do not cancel a thread and pretend the provider request stopped.
        if self.task is not None:
            await asyncio.gather(self.task, return_exceptions=True)
