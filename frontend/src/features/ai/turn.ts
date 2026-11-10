import type { AiStreamEvent, AiTurn } from "@/features/ai/api";

/** The turn being streamed, as shown in the chat. */
export type LiveTurn = Pick<
  AiTurn,
  "user_text" | "answer" | "tools" | "usage" | "status" | "error"
>;

export const EMPTY_USAGE = {
  input_tokens: 0,
  output_tokens: 0,
  cache_read_input_tokens: 0,
  cache_creation_input_tokens: 0,
};

/** Apply one stream event to the turn being written. */
export function applyEvent(turn: LiveTurn, ev: AiStreamEvent): LiveTurn {
  switch (ev.type) {
    case "text":
      return { ...turn, answer: turn.answer + ev.text };
    case "tool_start":
      return {
        ...turn,
        tools: [
          ...turn.tools,
          {
            id: ev.id,
            name: ev.name,
            args: ev.args,
            summary: "",
            is_error: false,
            truncated: false,
          },
        ],
      };
    case "tool_end":
      return {
        ...turn,
        tools: turn.tools.map((t) =>
          t.id === ev.id
            ? { ...t, summary: ev.summary, is_error: ev.is_error, truncated: ev.truncated }
            : t,
        ),
      };
    case "usage": {
      const { type: _type, ...usage } = ev;
      return { ...turn, usage };
    }
    case "error":
      return { ...turn, status: "error", error: ev.message };
    case "done":
      return { ...turn, status: ev.status };
  }
}
