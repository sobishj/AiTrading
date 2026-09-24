import { ReactNode, useEffect, useRef } from "react";
import { ImperativePanelGroupHandle, Panel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";

export type LayoutPreset = "balanced" | "chat" | "chart" | "analysis";

/** Panel sizes (%) for each group: outer [list, main], main [top, bottom], top [chart, plan], bottom [analysis, chat]. */
const PRESETS: Record<LayoutPreset, { outer: number[]; main: number[]; top: number[]; bottom: number[] }> = {
  balanced: { outer: [15, 85], main: [60, 40], top: [72, 28], bottom: [45, 55] },
  chat: { outer: [13, 87], main: [35, 65], top: [70, 30], bottom: [30, 70] },
  chart: { outer: [13, 87], main: [74, 26], top: [76, 24], bottom: [45, 55] },
  analysis: { outer: [13, 87], main: [38, 62], top: [70, 30], bottom: [65, 35] },
};

interface MainLayoutProps {
  topBar: ReactNode;
  stockList: ReactNode;
  briefBar: ReactNode;
  chart: ReactNode;
  tradePlan: ReactNode;
  analysis: ReactNode;
  chat: ReactNode;
  /** Changing `presetRequest.id` applies `presetRequest.preset` to every panel group. */
  presetRequest: { preset: LayoutPreset; id: number } | null;
}

function Handle() {
  return <PanelResizeHandle className="resize-handle" />;
}

/**
 * Approved PRD §5 layout, with every divider draggable:
 *  - top: application bar with search
 *  - left: stock names only (live ranked)
 *  - center: chart (with the slim morning-brief bar above it)
 *  - right: trade plan
 *  - bottom-left: AI analysis
 *  - bottom-right: AI chat
 * Sizes persist in this browser (autoSaveId); presets come from the top bar.
 */
export default function MainLayout({
  topBar, stockList, briefBar, chart, tradePlan, analysis, chat, presetRequest,
}: MainLayoutProps) {
  const outer = useRef<ImperativePanelGroupHandle>(null);
  const main = useRef<ImperativePanelGroupHandle>(null);
  const top = useRef<ImperativePanelGroupHandle>(null);
  const bottom = useRef<ImperativePanelGroupHandle>(null);

  useEffect(() => {
    if (!presetRequest) return;
    const sizes = PRESETS[presetRequest.preset];
    outer.current?.setLayout(sizes.outer);
    main.current?.setLayout(sizes.main);
    top.current?.setLayout(sizes.top);
    bottom.current?.setLayout(sizes.bottom);
  }, [presetRequest]);

  const d = PRESETS.balanced;

  return (
    <div className="h-screen w-screen flex flex-col overflow-hidden">
      {topBar}

      <div className="flex-1 min-h-0 p-3 pt-2">
        <PanelGroup ref={outer} direction="horizontal" autoSaveId="tradeai-layout-outer">
          <Panel defaultSize={d.outer[0]} minSize={8} maxSize={30} className="min-h-0">
            {stockList}
          </Panel>
          <Handle />
          <Panel defaultSize={d.outer[1]} minSize={50}>
            <PanelGroup ref={main} direction="vertical" autoSaveId="tradeai-layout-main">
              <Panel defaultSize={d.main[0]} minSize={20}>
                <PanelGroup ref={top} direction="horizontal" autoSaveId="tradeai-layout-top">
                  <Panel defaultSize={d.top[0]} minSize={30} className="flex flex-col gap-2 min-w-0">
                    <div className="shrink-0 min-w-0">{briefBar}</div>
                    <div className="flex-1 min-h-0 min-w-0">{chart}</div>
                  </Panel>
                  <Handle />
                  <Panel defaultSize={d.top[1]} minSize={15} className="min-h-0">
                    {tradePlan}
                  </Panel>
                </PanelGroup>
              </Panel>
              <Handle />
              <Panel defaultSize={d.main[1]} minSize={12}>
                <PanelGroup ref={bottom} direction="horizontal" autoSaveId="tradeai-layout-bottom">
                  <Panel defaultSize={d.bottom[0]} minSize={15} className="min-h-0 min-w-0">
                    {analysis}
                  </Panel>
                  <Handle />
                  <Panel defaultSize={d.bottom[1]} minSize={15} className="min-h-0 min-w-0">
                    {chat}
                  </Panel>
                </PanelGroup>
              </Panel>
            </PanelGroup>
          </Panel>
        </PanelGroup>
      </div>
    </div>
  );
}
