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

// Mobile browsers (iOS Safari strictly, Android Chrome more leniently) only
// allow HTMLMediaElement.play() when it is invoked synchronously inside a
// real user-gesture event handler. Every play() call in this module happens
// asynchronously instead -- after a fetch resolves, or after the VAD's own
// timer auto-stops a recording -- so on a mobile page every response was
// silently failing to play at all: the rejection was swallowed by speak()'s
// "one malformed chunk should not silence the rest" catch below, so nothing
// played and nothing looked broken either. Confirmed live as a real report
// ("the agent isn't speaking out loud" on mobile), not a hypothetical.
//
// Fixed with the standard technique for this exact restriction: a single,
// reused <audio> element, "unlocked" once by playing (and immediately
// pausing) a genuinely silent clip synchronously inside a real gesture. Once
// one specific element has successfully played from within a gesture, that
// same element instance keeps working from async code for the rest of the
// page's life -- but a *new* Audio() built later would not inherit that,
// which is why the old per-chunk `new Audio(...)` never actually recovered
// even after the user had already tapped the mic once. unlockAudioPlayback()
// must be called synchronously from every real click/tap handler that can
// lead to playback (App.tsx's mic button and "new booking" button,
// LocationLinkInput's submit) -- cheap and safe to call on every one of
// them unconditionally, since it no-ops once already unlocked.
//
// The clip itself: 100ms of true 8-bit PCM silence (all samples at the
// midpoint, not a loud clip muted in code), generated with Python's `wave`
// module rather than hand-built or copied from memory, so its bytes are
// known-correct rather than assumed.
const _SILENT_WAV =
  "data:audio/wav;base64,UklGRkQDAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YSADAACAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgA==";

let sharedAudioEl: HTMLAudioElement | null = null;
let audioUnlocked = false;

function getSharedAudio(): HTMLAudioElement {
  if (!sharedAudioEl) sharedAudioEl = new Audio();
  return sharedAudioEl;
}

/** Call synchronously, as the first thing inside a real click/tap handler --
 * see the block comment above. Idempotent: a no-op after the first
 * successful call, and safe to call unconditionally on every gesture. */
export function unlockAudioPlayback(): void {
  if (audioUnlocked) return;
  audioUnlocked = true; // set before the async play() settles, so an overlapping
  // second gesture while this one is still pending does not race a second attempt
  const el = getSharedAudio();
  el.src = _SILENT_WAV;
  el.play()
    .then(() => el.pause())
    .catch(() => {
      // A gesture that still fails to unlock (a browser with audio hard-
      // disabled, say) should not break the click handler it ran inside --
      // the next real playback attempt just fails the same way it always
      // would have, no worse off than before this existed.
    });
}

function playOne(base64Wav: string, registerStop: (stop: () => void) => void): Promise<void> {
  return new Promise((resolve, reject) => {
    const audio = getSharedAudio();
    // Explicit removal (not just {once: true}) because this element is now
    // reused across every chunk of every turn: `finish` fired via the stop()
    // path (a direct call, not the "ended" event) never triggers {once:true}'s
    // own auto-removal, which would otherwise leak a stale "ended"/"error"
    // listener onto the *next* chunk's playback.
    const finish = () => {
      audio.removeEventListener("ended", finish);
      audio.removeEventListener("error", onError);
      resolve();
    };
    const onError = () => {
      audio.removeEventListener("ended", finish);
      audio.removeEventListener("error", onError);
      reject(new Error("audio playback failed"));
    };
    registerStop(() => {
      audio.pause();
      finish(); // pausing alone never fires "ended" -- resolve explicitly so the caller's loop can move on
    });
    audio.addEventListener("ended", finish, { once: true });
    audio.addEventListener("error", onError, { once: true });
    audio.src = `data:audio/wav;base64,${base64Wav}`;
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
