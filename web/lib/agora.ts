"use client";

import type { IAgoraRTCClient, IMicrophoneAudioTrack } from "agora-rtc-sdk-ng";

export type VoiceHandle = {
  leave: () => Promise<void>;
  setMuted: (muted: boolean) => Promise<void>;
};

type Opts = {
  appId: string;
  channel: string;
  token: string | null;
  uid: number;
  onAgentLevel: (level: number) => void; // 0..100, agent audio
  onMicLevel: (level: number) => void; // 0..100, user mic
  onAgentJoined: () => void;
  onAgentLeft: () => void;
};

/** Join the RTC channel the ConvoAI agent is in, publish the mic, play the agent's audio. */
export async function joinVoice(o: Opts): Promise<VoiceHandle> {
  const AgoraRTC = (await import("agora-rtc-sdk-ng")).default;
  AgoraRTC.setLogLevel(3);
  const client: IAgoraRTCClient = AgoraRTC.createClient({ mode: "rtc", codec: "vp8" });

  client.on("user-joined", () => o.onAgentJoined());
  client.on("user-left", () => o.onAgentLeft());
  client.on("user-published", async (user, mediaType) => {
    await client.subscribe(user, mediaType);
    if (mediaType === "audio") user.audioTrack?.play();
  });
  client.enableAudioVolumeIndicator();
  client.on("volume-indicator", (vols: { uid: number | string; level: number }[]) => {
    let agent = 0;
    let me = 0;
    for (const v of vols) {
      if (String(v.uid) === String(o.uid) || v.uid === 0) me = Math.max(me, v.level);
      else agent = Math.max(agent, v.level);
    }
    o.onAgentLevel(agent);
    o.onMicLevel(me);
  });

  await client.join(o.appId, o.channel, o.token || null, o.uid);
  const mic: IMicrophoneAudioTrack = await AgoraRTC.createMicrophoneAudioTrack({
    AEC: true,
    ANS: true,
    AGC: true,
    encoderConfig: "speech_standard",
  });
  await client.publish([mic]);

  return {
    setMuted: (muted) => mic.setEnabled(!muted),
    leave: async () => {
      mic.stop();
      mic.close();
      await client.leave();
    },
  };
}
