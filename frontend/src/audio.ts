/** Playing back what /api/session and /api/turn return: a list of
 * base64-encoded WAV chunks, played back to back -- never concatenated,
 * since services/tts.py deliberately returns them as separate files (a
 * WAV header encodes a single length; gluing complete WAV files together
 * produces a malformed one). Falls back to the browser's speechSynthesis
 * when tts_fallback is set, matching the backend's own documented escape
 * hatch for when Groq TTS is unavailable.
 */

function speakWithBrowserTts(text: string, onCancel: (cancel: () => void) => void): Promise<void> {
  return new Promise((resolve) => {
    if (!("speechSynthesis" in window) || !text) {
      resolve();
      return;
    }
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.onend = () => resolve();
    utterance.onerror = () => resolve(); // a failed (or cancelled) fallback should not block the UI
    onCancel(() => window.speechSynthesis.cancel());
    window.speechSynthesis.speak(utterance);
  });
}

export type SpeechHandle = {
  /** Resolves once playback finishes on its own, or is stopped early. */
  finished: Promise<void>;
  /** Cuts the current response off immediately -- the "stop talking"
   * control, and also called automatically when the user taps the mic
   * again while the agent is still speaking (talking over it is the
   * natural way to interrupt a voice assistant). */
  stop: () => void;
};

function playOne(base64Wav: string, registerStop: (stop: () => void) => void): Promise<void> {
  return new Promise((resolve, reject) => {
    const audio = new Audio(`data:audio/wav;base64,${base64Wav}`);
    const finish = () => resolve();
    registerStop(() => {
      audio.pause();
      finish(); // pausing alone never fires "ended" -- resolve explicitly so the caller's loop can move on
    });
    audio.addEventListener("ended", finish, { once: true });
    audio.addEventListener("error", () => reject(new Error("audio playback failed")), { once: true });
    audio.play().catch(reject);
  });
}

export function speak(audioChunks: string[], ttsFallback: boolean, text: string): SpeechHandle {
  let stopped = false;
  let cancelCurrent: (() => void) | null = null;

  const stop = () => {
    stopped = true;
    cancelCurrent?.();
  };

  const run = async () => {
    if (ttsFallback || audioChunks.length === 0) {
      await speakWithBrowserTts(text, (cancel) => {
        cancelCurrent = cancel;
      });
      return;
    }
    for (const chunk of audioChunks) {
      if (stopped) break;
      try {
        await playOne(chunk, (cancel) => {
          cancelCurrent = cancel;
        });
      } catch {
        // One malformed chunk should not silence the rest of the response.
      }
    }
  };

  return { finished: run(), stop };
}
