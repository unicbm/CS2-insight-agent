import { useState } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import { useLocaleStore } from "../i18n/localeStore.js";
import ClipCard from "./ClipCard.jsx";
import OptionalAiReviewSettings from "./OptionalAiReviewSettings.jsx";

afterEach(() => {
  cleanup();
  useLocaleStore.setState({ locale: "zh" });
});

const translations = {
  "settings.sectionAnalysisMode": "分析模式",
  "settings.sectionAnalysisModeHint": "AI 可选",
  "settings.modeLocal": "极速本地",
  "settings.modeLocalDesc": "不请求 AI",
  "settings.modeAi": "AI 洞察",
  "settings.modeAiDesc": "生成锐评",
  "settings.sectionLlm": "大模型 (AI)",
  "settings.sectionLlmHint": "OpenAI 兼容配置",
  "settings.localEndpointHint": "本机地址无需密钥",
  "settings.labelLlmBaseUrl": "接口地址",
  "settings.labelLlmModel": "模型名称",
  "settings.labelLlmApiKey": "API 密钥",
  "settings.baseUrlPlaceholder": "http://localhost:11434/v1",
  "settings.modelPlaceholder": "model-name",
  "settings.apiKeyPlaceholderKeep": "留空保留",
};

const t = (key) => translations[key] ?? key;

describe("OptionalAiReviewSettings", () => {
  test("本地模式仅显示二选一，切到 AI 后才展开配置字段", () => {
    const onLlmChange = vi.fn();

    function Harness() {
      const [enabled, setEnabled] = useState(false);
      const [llm, setLlm] = useState({ base_url: "", model: "", api_key: "" });
      return (
        <OptionalAiReviewSettings
          enabled={enabled}
          onEnabledChange={setEnabled}
          llm={llm}
          onLlmChange={(next) => {
            onLlmChange(next);
            setLlm(next);
          }}
          t={t}
        />
      );
    }

    render(<Harness />);

    expect(screen.getByRole("button", { name: /极速本地/ }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.queryByLabelText("接口地址")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /AI 洞察/ }));

    expect(screen.getByLabelText("接口地址")).toBeTruthy();
    expect(screen.getByLabelText("模型名称")).toBeTruthy();
    expect(screen.getByLabelText("API 密钥")).toBeTruthy();

    fireEvent.change(screen.getByLabelText("模型名称"), { target: { value: "qwen-local" } });
    expect(onLlmChange).toHaveBeenLastCalledWith(expect.objectContaining({ model: "qwen-local" }));
  });
});

describe("ClipCard AI visibility", () => {
  test("只在 AI 模式显示已有分数与锐评", () => {
    useLocaleStore.setState({ locale: "zh" });
    const clip = {
      clip_id: "clip-1",
      client_clip_uid: "clip-1",
      category: "highlight",
      round: 3,
      start_tick: 100,
      end_tick: 200,
      context_tags: [],
      victims: [],
      weapon_used: "",
      ai_score: 91,
      ai_commentary: "这条精准锐评只应在 AI 模式出现",
    };
    const props = {
      clip,
      selected: false,
      onToggle: vi.fn(),
    };

    const { rerender } = render(<ClipCard {...props} aiMode={false} />);
    expect(screen.queryByText(clip.ai_commentary)).toBeNull();
    expect(screen.queryByLabelText(/AI.*91/)).toBeNull();

    rerender(<ClipCard {...props} aiMode />);
    expect(screen.getByText(clip.ai_commentary)).toBeTruthy();
    expect(screen.getByLabelText(/AI.*91/)).toBeTruthy();
  });
});
