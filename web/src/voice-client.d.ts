export interface VoiceCall {
  maxSeconds: number;
  hangUp(): void;
}
export function startCall(options: {
  apiUrl: string;
  token?: string;
  incidentId: string;
  residentId: string;
  on?: Record<string, (...args: never[]) => void>;
}): Promise<VoiceCall>;
