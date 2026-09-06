import { useCallback, useEffect, useRef, useState } from "react";

export type RecorderStatus = "idle" | "recording" | "processing" | "error";

// A *fixed* RMS threshold was the first version here, and real-device testing
// showed it badly miscalibrated -- recordings ran long because "speech" or
// "silence" never crossed a hardcoded number that had no relationship to the
// user's actual microphone, room noise, or the browser's own automatic gain
// control (which getUserMedia({audio:true}) enables by default and which
// continuously renormalises levels, exactly the kind of thing a fixed cutoff
// cannot track). Replaced with an adaptive floor: the "quiet" level is
// measured continuously from the live stream itself, and speech is judged
// relative to that running estimate, not to a number picked in advance.
const SPEECH_MARGIN_ABOVE_FLOOR = 0.018; // how far above the tracked floor counts as speech
const FLOOR_ADAPT_RATE = 0.05; // how fast the floor follows the ambient level while quiet
const INITIAL_NOISE_FLOOR = 0.01; // conservative seed; corrects itself within a few frames
const SILENCE_DURATION_MS = 700; // matches MASTER_PLAN.md's 3.5 acceptance criterion
const MAX_RECORDING_MS = 20_000; // safety net if speech is never detected at all

const PREFERRED_MIME_TYPES = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus"];

function pickSupportedMimeType(): string {
  for (const type of PREFERRED_MIME_TYPES) {
    if (MediaRecorder.isTypeSupported(type)) return type;
  }
  return ""; // let the browser choose its own default rather than fail outright
}

/** Mic capture with adaptive voice-activity detection: recording starts on
 * demand and stops itself once the user has spoken and then gone quiet for
 * SILENCE_DURATION_MS -- no push-to-talk, no manual "done" step. Silence is
 * only ever measured *after* real speech is first detected, so a thoughtful
 * pause before speaking is never mistaken for "finished talking".
 */
export function useRecorder(onComplete: (audio: Blob, mimeType: string) => void) {
  const [status, setStatus] = useState<RecorderStatus>("idle");
  const [error, setError] = useState<string | null>(null);

  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const rafRef = useRef<number | null>(null);
  const maxDurationTimerRef = useRef<number | null>(null);

  const cleanup = useCallback(() => {
    if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
    if (maxDurationTimerRef.current !== null) window.clearTimeout(maxDurationTimerRef.current);
    rafRef.current = null;
    maxDurationTimerRef.current = null;
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    if (audioContextRef.current && audioContextRef.current.state !== "closed") {
      void audioContextRef.current.close();
    }
    audioContextRef.current = null;
  }, []);

  useEffect(() => cleanup, [cleanup]);

  const stop = useCallback(() => {
    if (recorderRef.current && recorderRef.current.state === "recording") {
      recorderRef.current.stop();
    }
  }, []);

  const start = useCallback(async () => {
    if (status === "recording" || status === "processing") return;
    setError(null);

    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      setStatus("error");
      setError("Microphone access was denied or is unavailable.");
      return;
    }
    streamRef.current = stream;

    const mimeType = pickSupportedMimeType();
    const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
    recorderRef.current = recorder;
    const chunks: BlobPart[] = [];

    recorder.addEventListener("dataavailable", (event) => {
      if (event.data.size > 0) chunks.push(event.data);
    });
    recorder.addEventListener("stop", () => {
      const blob = new Blob(chunks, { type: recorder.mimeType || mimeType || "audio/webm" });
      cleanup();
      setStatus("processing");
      onComplete(blob, recorder.mimeType || mimeType || "audio/webm");
    });

    recorder.start();
    setStatus("recording");

    maxDurationTimerRef.current = window.setTimeout(stop, MAX_RECORDING_MS);

    // Voice-activity detection: independent of MediaRecorder, reading the
    // same live stream through the Web Audio API.
    const audioContext = new AudioContext();
    audioContextRef.current = audioContext;
    const source = audioContext.createMediaStreamSource(stream);
    const analyser = audioContext.createAnalyser();
    analyser.fftSize = 2048;
    source.connect(analyser);
    const timeDomain = new Uint8Array(analyser.frequencyBinCount);

    let hasDetectedSpeech = false;
    let silenceStartedAt: number | null = null;
    let noiseFloor = INITIAL_NOISE_FLOOR;
    let lastLogAt = 0;

    const tick = () => {
      analyser.getByteTimeDomainData(timeDomain);
      let sumSquares = 0;
      for (let i = 0; i < timeDomain.length; i++) {
        const normalized = (timeDomain[i] - 128) / 128;
        sumSquares += normalized * normalized;
      }
      const rms = Math.sqrt(sumSquares / timeDomain.length);
      const speechThreshold = noiseFloor + SPEECH_MARGIN_ABOVE_FLOOR;
      const isSpeech = rms >= speechThreshold;

      if (isSpeech) {
        hasDetectedSpeech = true;
        silenceStartedAt = null;
      } else {
        // Only track the floor while quiet, so a loud word never drags the
        // baseline up and makes the *next* word look like more silence.
        noiseFloor += (rms - noiseFloor) * FLOOR_ADAPT_RATE;
        if (hasDetectedSpeech) {
          if (silenceStartedAt === null) {
            silenceStartedAt = performance.now();
          } else if (performance.now() - silenceStartedAt >= SILENCE_DURATION_MS) {
            stop();
            return;
          }
        }
      }

      // Throttled diagnostic: cheap enough to leave in permanently, and the
      // one thing that let a real VAD miscalibration (see the constants
      // above) be diagnosed and fixed after a real-device report instead of
      // guessed at blind, since this environment has no microphone to test
      // against directly.
      const now = performance.now();
      if (now - lastLogAt > 2000) {
        lastLogAt = now;
        console.debug(
          `[vad] rms=${rms.toFixed(4)} floor=${noiseFloor.toFixed(4)} ` +
            `threshold=${speechThreshold.toFixed(4)} speaking=${isSpeech} ` +
            `hasSpoken=${hasDetectedSpeech}`,
        );
      }

      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
  }, [status, cleanup, onComplete, stop]);

  return { status, error, start, stop };
}
