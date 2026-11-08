import { describe, expect, it } from "vitest";
import type { Schemas } from "@/features/people/apiTypes";
import { buildSlides, slideAltText } from "@/features/wrapped/slides";

const me = { person_id: 1, name: "Me", color_index: 0, is_owner: true };
const jana = { person_id: 2, name: "Jana", color_index: 1, is_owner: false };

function wrapped(over: Partial<Schemas["WrappedResponse"]> = {}): Schemas["WrappedResponse"] {
  return {
    dataset_version: 1,
    year: 2024,
    available_years: [2024],
    total_messages: 100,
    sent_by_me: 60,
    received: 40,
    total_drilldown: { start: "2024-01-01", end: "2024-12-31" },
    months: [{ month: "2024-01-01", messages: 100 }],
    top_people: [{ person: jana, messages: 40 }],
    top_emoji: { emoji: "😂", count: 9 },
    top_emojis: [
      { emoji: "😂", count: 9 },
      { emoji: "❤", count: 3 },
    ],
    busiest_day: {
      date: "2024-03-05",
      messages: 30,
      drilldown: { start: "2024-03-05", end: "2024-03-05" },
    },
    night_owl_share: 0.2,
    my_night_owl_share: 0.25,
    longest_streak: { days: 5, start: "2024-03-01", end: "2024-03-05" },
    top_word: "ahoj",
    top_words: [{ word: "ahoj", count: 12 }],
    fastest_replier: { person: jana, median_ms: 60000, replies: 10 },
    most_reacted_message: {
      message: {
        message_id: 7,
        thread_id: 3,
        sender: me,
        ts: "2024-03-05T10:00:00Z",
        type: "text",
        text: "hi",
      },
      thread_title: "Jana",
      reactions: 4,
      emojis: ["❤"],
      drilldown: { message_ids: [7] },
    },
    longest_call: null,
    personality: { summary: "You are 😂 Comedian.", awards: [] },
    ...over,
  } as Schemas["WrappedResponse"];
}

describe("buildSlides", () => {
  it("builds one slide per available item and skips missing ones", () => {
    const keys = buildSlides(wrapped()).map((s) => s.key);
    expect(keys).toEqual([
      "intro",
      "total",
      "people",
      "emoji",
      "busiest",
      "night",
      "streak",
      "word",
      "replier",
      "reacted",
      "personality",
      "outro",
    ]);
  });
  it("hides text in anonymize mode", () => {
    const w = wrapped({
      top_word: null,
      top_words: [],
      most_reacted_message: {
        ...wrapped().most_reacted_message!,
        message: { ...wrapped().most_reacted_message!.message, text: null },
      },
    });
    const slides = buildSlides(w, { anonymize: true });
    const word = slides.find((s) => s.key === "word")!;
    expect(slideAltText(word)).toContain("Hidden in anonymize mode");
    const reacted = slides.find((s) => s.key === "reacted")!;
    expect(slideAltText(reacted)).toContain("Message text hidden");
  });
  it("converts drilldowns to Message Browser queries", () => {
    const busiest = buildSlides(wrapped()).find((s) => s.key === "busiest")!;
    expect(busiest.drilldown).toEqual({ start: "2024-03-05", end: "2024-03-05" });
  });
  it("handles an empty year", () => {
    expect(buildSlides(wrapped({ total_messages: 0 })).map((s) => s.key)).toEqual([
      "intro",
      "empty",
    ]);
  });
});
