/** Playing back what /api/session and /api/turn return: a list of
 * base64-encoded WAV chunks, played back to back -- never concatenated,
 * since services/tts.py deliberately returns them as separate files (a
 * WAV header encodes a single length; gluing complete WAV files together
 * produces a malformed one). Falls back to the browser's speechSynthesis
 * when tts_fallback is set, matching the backend's own documented escape
 * hatch for when Groq TTS is unavailable.
 */

function playOne(base64Wav: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const audio = new Audio(`data:audio/wav;base64,${base64Wav}`);
    audio.addEventListener("ended", () => resolve(), { once: true });
    audio.addEventListener("error", () => reject(new Error("audio playback failed")), {
      once: true,
    });
    audio.play().catch(reject);
  });
}

function speakWithBrowserTts(text: string): Promise<void> {
  return new Promise((resolve) => {
    if (!("speechSynthesis" in window) || !text) {
      resolve();
      return;
    }
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.onend = () => resolve();
    utterance.onerror = () => resolve(); // a failed fallback should not block the UI
    window.speechSynthesis.speak(utterance);
  });
}

export async function speak(
  audioChunks: string[],
  ttsFallback: boolean,
  text: string,
): Promise<void> {
  if (ttsFallback || audioChunks.length === 0) {
    await speakWithBrowserTts(text);
    return;
  }
  for (const chunk of audioChunks) {
    try {
      await playOne(chunk);
    } catch {
      // One malformed chunk should not silence the rest of the response.
    }
  }
}
