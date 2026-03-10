<script>
    import { onDestroy, onMount } from "svelte";
    import { ChevronDown, ChevronUp } from "@lucide/svelte";

    // visibleDistributionSummary: Array<{ result, spots: [{ barcode, index, cluster, color? }], clusters?: [{ cluster, color }] }>
    export let visibleDistributionSummary = [];
    export let selectedResultId = null;
    // Metrics data: Map<cluster_result_id, { chao?, silhouette?, pas?, morans_i? }>
    export let metricsData = new Map();
    // Cluster-level metrics: Map<cluster_result_id, Map<cluster, { silhouette?, morans_i?, gearys_c? }>>
    export let clusterMetricsData = new Map();

    // Optional overrides
    export let labelOrderOverride = null; // string[] | null
    export let title = "Cluster Comparison Matrix";
    // Sankey dimensions for matching layout
    // After rotation: rowPositions contains screen x for each result column
    export let sankeyDimensions = { paddingLeft: 24, sankeyWidth: 0, totalWidth: 0, screenWidth: 0, rowPositions: [], rowHeight: 0 };
    
    // Collapse state
    export let isCollapsed = false;
    const COLLAPSED_HEIGHT = 40;

    // Performance controls
    export let maxSpotsPerCell = 220; // sample cap per (result,label) cell
    export let maxSpotLabelsPerCell = 40; // sample cap for text labels per cell
    export let showSpotLabels = true;
    // How far (in px) to offset spot text along the radial tangent direction.
    // Smaller = "shorter tangential" placement (text closer to the dot).
    export let tangentLabelOffset = 1.2;
    // Radial line control: line starts from innerRadius and extends to cellRadius
    export let spotRadialLineInnerRatio = 0.9; // 0-1, where 0 = center, 1 = edge
    export let spotRadialLineWidth = 0.5; // stroke width in px
    export let showMetricsChart = true;

    // How to show spot label text
    // By default, we DO NOT render barcode text labels on the chart.
    // Provide your own accessor to render the real "spot label" (e.g. celltype / annotation).
    // Signature: (barcode: string, ctx?: { spotIndex, resultIndex, label, summary }) => string
    export let spotLabelAccessor = () => "";

    let containerEl;
    let containerWidth = 0;
    let containerHeight = 0;
    let ro = null;

    // Hover state
    let hover = null; // { x,y, barcode, spotLabel, label, resultLabel, color }
    let metricsHover = null; // { x,y, resultIndex, resultLabel, metricKey, metricLabel, value, displayValue, color, allMetrics: { key: { label, value, color } } }
    let radarHover = null; // { x,y, resultId, resultLabel, label, clusterLabel, metrics: { silhouette, morans_i, gearys_c } }

    let mainSvgEl;

    const TAU = Math.PI * 2;
    const COMBINED_GAP = 16;

    // Result-level metrics (for stacked area chart)
    const metricKeys = ["chao", "silhouette", "pas", "morans_i"];
    const metricLabels = {
        chao: "CH",
        silhouette: "Silhouette",
        pas: "PAS",
        morans_i: "Moran's I",
    };
    // Theme blue-gray color for all metrics
    const themeBlueGray = "rgba(71, 85, 105, 0.8)"; // slate-600
    const themeBlueGrayLight = "rgba(71, 85, 105, 0.5)"; // lighter variant
    
    const metricColors = {
        chao: themeBlueGrayLight,
        silhouette: themeBlueGrayLight,
        pas: themeBlueGrayLight,
        morans_i: themeBlueGrayLight,
    };
    const metricStrokeColors = {
        chao: themeBlueGray,
        silhouette: themeBlueGray,
        pas: themeBlueGray,
        morans_i: themeBlueGray,
    };
    const metricFillPatterns = {
        chao: "white",                    // hollow
        silhouette: themeBlueGrayLight,   // solid gray
        pas: "url(#pattern-diagonal)",    // diagonal hatching
        morans_i: "url(#pattern-dots)",   // dots
    };
    const metricFillLabels = {
        chao: "Hollow",
        silhouette: "Solid",
        pas: "Hatching",
        morans_i: "Dots",
    };
    const metricDescriptions = {
        chao: "CH (Calinski-Harabasz): within-cluster cohesion vs between-cluster separation, higher is better",
        silhouette: "Silhouette score: cluster quality in [-1, 1], higher is better",
        pas: "Percentage of abnormal spots: neighbors with different label, lower is better",
        morans_i: "Moran's I: spatial autocorrelation, higher means stronger spatial clustering, higher is better",
    };

    // Cluster-level metrics (for radar charts) - only 3 metrics
    const clusterMetricKeys = ["silhouette", "morans_i", "gearys_c"];
    const clusterMetricLabels = {
        silhouette: "Silhouette",
        morans_i: "Moran's I",
        gearys_c: "Geary's C",
    };
    const clusterMetricDescriptions = {
        silhouette: "Within-cluster silhouette, higher is better",
        morans_i: "Within-cluster spatial autocorrelation, higher means stronger spatial aggregation, higher is better",
        gearys_c: "Geary's C spatial autocorrelation, closer to 0 means stronger positive spatial correlation, lower is better",
    };

    function normalize01(v, min, max) {
        if (!Number.isFinite(v)) return 0;
        if (!Number.isFinite(min) || !Number.isFinite(max) || max === min) return 0;
        return clamp((v - min) / (max - min), 0, 1);
    }

    // Metrics can come from API as strings; normalize to finite numbers.
    function toMetricNumber(v) {
        if (v === null || v === undefined) return 0;
        const n = typeof v === "number" ? v : Number(v);
        return Number.isFinite(n) ? n : 0;
    }


    const getResultLabel = (summary) => {
        const result = summary?.result;
        return (
            result?.result_name ||
            result?.cluster_result_id ||
            result?.result_id ||
            "Unnamed result"
        );
    };

    function computeSpotOrder(summaries) {
        if (!summaries?.length) return [];
        const first = summaries[0];
        return (first?.spots ?? [])
            .slice()
            .sort((a, b) => (a?.index ?? 0) - (b?.index ?? 0))
            .map((s) => `${s?.barcode ?? ""}`.trim())
            .filter(Boolean);
    }

    function computeLabelOrderFromClusters(summaries) {
        const set = new Set();
        for (const summary of summaries ?? []) {
            for (const c of summary?.clusters ?? []) {
                const key = `${c?.cluster ?? ""}`.trim();
                if (key) set.add(key);
            }
        }
        // Fallback: derive from spots.cluster if clusters list missing
        if (!set.size) {
            for (const summary of summaries ?? []) {
                for (const s of summary?.spots ?? []) {
                    const key =
                        s?.cluster === null || s?.cluster === undefined
                            ? ""
                            : `${s.cluster}`.trim();
                    if (key) set.add(key);
                }
            }
        }
        const arr = Array.from(set);
        arr.sort((a, b) => {
            const na = Number.parseFloat(a);
            const nb = Number.parseFloat(b);
            const aNum = !Number.isNaN(na);
            const bNum = !Number.isNaN(nb);
            if (aNum && bNum) return na - nb;
            if (aNum && !bNum) return -1;
            if (!aNum && bNum) return 1;
            return a.localeCompare(b, undefined, { numeric: true });
        });
        return arr;
    }

    function buildBarcodeToClusterMap(summary) {
        const map = new Map();
        for (const s of summary?.spots ?? []) {
            const bc = `${s?.barcode ?? ""}`.trim();
            if (!bc) continue;
            const c = s?.cluster;
            const key = c === null || c === undefined ? "" : `${c}`.trim();
            map.set(bc, key || null);
        }
        return map;
    }

    function getClusterColor(summary, clusterKey) {
        if (!summary?.clusters || !clusterKey) return null;
        const found = summary.clusters.find(
            (c) => `${c?.cluster ?? ""}`.trim() === `${clusterKey}`.trim(),
        );
        return found?.color || null;
    }

    // Get cluster metrics for a specific result and cluster
    function getClusterMetrics(resultId, clusterKey) {
        if (!resultId || !clusterKey) return null;
        const clusterMap = clusterMetricsData.get(resultId);
        if (!clusterMap) return null;
        const clusterName = `${clusterKey}`.trim();
        let metrics = clusterMap.get(clusterName);
        if (!metrics) {
            // Try numeric variants
            const num = Number.parseFloat(clusterName);
            if (!Number.isNaN(num)) {
                metrics = clusterMap.get(num.toFixed(1)) || clusterMap.get(String(num));
            }
        }
        return metrics || null;
    }

    // Get metric range across all clusters for normalization
    function getMetricRange(metricKey) {
        let min = null;
        let max = null;
        clusterMetricsData.forEach((clusterMap) => {
            clusterMap.forEach((m) => {
                const val = toMetricNumber(m?.[metricKey]);
                if (val !== 0) {
                    if (min === null || val < min) min = val;
                    if (max === null || val > max) max = val;
                }
            });
        });
        if (min === null || max === null) {
            // Default ranges based on metric type
            if (metricKey === "silhouette") return { min: -1, max: 1 };
            if (metricKey === "morans_i") return { min: -1, max: 1 };
            if (metricKey === "gearys_c") return { min: 0, max: 2 };
            return { min: 0, max: 1 };
        }
        const padding = (max - min) * 0.1 || 0.1;
        return { min: min - padding, max: max + padding };
    }

    // Normalize metric value to 0-1 range
    // For metrics where lower is better (e.g., gearys_c), invert the normalized value
    function normalizeMetricValue(value, metricKey) {
        const range = getMetricRange(metricKey);
        if (range.max === range.min) return 0.5;
        let normalized = (value - range.min) / (range.max - range.min);
        normalized = Math.max(0, Math.min(1, normalized));
        // Geary's C: lower is better, so invert
        if (metricKey === "gearys_c") {
            normalized = 1 - normalized;
        }
        return normalized;
    }

    function clamp(v, lo, hi) {
        return Math.max(lo, Math.min(hi, v));
    }

    // Generate smooth curved path for radar chart (irregular circle shape)
    function generateSmoothRadarPath(points, closed = true) {
        if (!points || points.length < 2) return "";
        if (points.length === 2) {
            return `M ${points[0].x.toFixed(2)} ${points[0].y.toFixed(2)} L ${points[1].x.toFixed(2)} ${points[1].y.toFixed(2)}`;
        }
        
        let d = `M ${points[0].x.toFixed(2)} ${points[0].y.toFixed(2)}`;
        const n = points.length;
        
        for (let i = 0; i < n; i++) {
            const p0 = points[(i - 1 + n) % n];
            const p1 = points[i];
            const p2 = points[(i + 1) % n];
            const p3 = points[(i + 2) % n];
            
            // Calculate control points for smooth curve
            const cp1x = p1.x + (p2.x - p0.x) * 0.15;
            const cp1y = p1.y + (p2.y - p0.y) * 0.15;
            const cp2x = p2.x - (p3.x - p1.x) * 0.15;
            const cp2y = p2.y - (p3.y - p1.y) * 0.15;
            
            if (i === 0) {
                d += ` L ${cp1x.toFixed(2)} ${cp1y.toFixed(2)}`;
            }
            d += ` C ${cp1x.toFixed(2)} ${cp1y.toFixed(2)}, ${cp2x.toFixed(2)} ${cp2y.toFixed(2)}, ${p2.x.toFixed(2)} ${p2.y.toFixed(2)}`;
        }
        
        if (closed) {
            d += " Z";
        }
        
        return d;
    }

    function formatSpotLabel(barcode, ctx) {
        try {
            const v = spotLabelAccessor ? spotLabelAccessor(barcode, ctx) : "";
            return v === null || v === undefined ? "" : String(v);
        } catch {
            return "";
        }
    }

    function stableIdForSummary(summary, idx) {
        return (
            summary?.result?.cluster_result_id ||
            summary?.result?.result_id ||
            summary?.result?.result_name ||
            idx
        );
    }

    function textRotationForTangent(tangentAngleRad) {
        let deg = (tangentAngleRad * 180) / Math.PI;
        // Normalize to [0, 360)
        deg = ((deg % 360) + 360) % 360;
        const flip = deg > 90 && deg < 270;
        if (flip) deg = (deg + 180) % 360;
        return { deg, flip };
    }

    function handleSpotEnter(e, payload) {
        hover = {
            x: e.clientX,
            y: e.clientY,
            ...payload,
        };
    }

    function handleSpotMove(e) {
        if (!hover) return;
        hover = { ...hover, x: e.clientX, y: e.clientY };
    }

    function handleSpotLeave() {
        hover = null;
    }

    function handleMetricsPointerLeave() {
        metricsHover = null;
    }


    function handleRadarEnter(e, payload) {
        radarHover = {
            x: e.clientX,
            y: e.clientY,
            ...payload,
        };
    }

    function handleRadarMove(e) {
        if (!radarHover) return;
        radarHover = { ...radarHover, x: e.clientX, y: e.clientY };
    }

    function handleRadarLeave() {
        radarHover = null;
    }

    onMount(() => {
        if (typeof ResizeObserver === "undefined") return;
        ro = new ResizeObserver((entries) => {
            const entry = entries?.[0];
            const cr = entry?.contentRect;
            if (!cr) return;
            containerWidth = Math.max(1, cr.width);
            containerHeight = Math.max(1, cr.height);
        });
        if (containerEl) ro.observe(containerEl);
    });

    onDestroy(() => {
        ro?.disconnect();
        ro = null;
    });

    $: summaries = visibleDistributionSummary ?? [];
    $: spotOrder = computeSpotOrder(summaries);
    $: labelOrder =
        Array.isArray() && labelOrderOverride.length
            ? labelOrderOverride
            : computeLabelOrderFromClusters(summaries);
    $: barcodeMaps = summaries.map((s) => buildBarcodeToClusterMap(s));

    // Grid layout: rows = labels, cols = results (swapped)
    $: resultCount = summaries.length;
    $: labelCount = labelOrder.length;
    
    // Calculate grid dimensions
    $: cellGap = 16;
    $: rowHeaderWidth = 0; // no row headers needed
    $: colHeaderHeight = 0; // no column headers
    $: availableWidth = Math.max(1, (containerWidth || 800) - rowHeaderWidth - 40);
    $: metricsOverhead = showTopMetrics ? metricsChartHeight + 4 : 0;
    $: availableHeight = Math.max(1, (containerHeight || 600) - colHeaderHeight - 40 - metricsOverhead);
    
    // Fixed cell size (all circles same size)
    // When results are too many, allow horizontal scrolling instead of compressing cellSize
    $: cellSize = (() => {
        if (resultCount === 0 || labelCount === 0) return 60;
        // Only consider height constraint, not width - allow horizontal scrolling
        const maxCellByHeight = (availableHeight - (labelCount - 1) * cellGap) / labelCount; // rows = labels
        return Math.min(80, Math.max(40, maxCellByHeight));
    })();
    
    $: cellRadius = cellSize / 2 - 4; // circle radius within cell (with padding)
    // Use sankey screen width for grid width if available, otherwise fall back to calculated width
    $: gridWidth = sankeyDimensions?.screenWidth > 0 
        ? sankeyDimensions.screenWidth 
        : resultCount * cellSize + (resultCount - 1) * cellGap; // columns = results
    $: gridHeight = labelCount * cellSize + (labelCount - 1) * cellGap; // rows = labels

    // ---- Metrics stacked area chart (top, rotated 90°) ----

    $: metricsByResult = summaries.map((summary) => {
        const resultId = summary?.result?.cluster_result_id ?? summary?.result?.result_id;
        const m = metricsData?.get?.(resultId)
            ?? metricsData?.get?.(String(resultId))
            ?? metricsData?.get?.(Number(resultId))
            ?? summary?.result?.metrics
            ?? {};
        return { resultId, metrics: m };
    });

    $: metricRanges = (() => {
        const ranges = {};
        for (const k of metricKeys) {
            let min = Infinity;
            let max = -Infinity;
            for (const row of metricsByResult) {
                const raw = row.metrics?.[k];
                if (raw === null || raw === undefined) continue;
                const v = toMetricNumber(raw);
                if (!Number.isFinite(v)) continue;
                if (v < min) min = v;
                if (v > max) max = v;
            }
            if (min === Infinity || max === -Infinity) {
                ranges[k] = { min: 0, max: 1 };
            } else {
                // Floor to 0 for bar charts so bars show absolute magnitude
                const barMin = Math.min(0, min);
                const barMax = max === barMin ? barMin + 1 : max;
                ranges[k] = { min: barMin, max: barMax };
            }
        }
        return ranges;
    })();

    $: hasAnyMetrics = (() => {
        if (!metricsByResult?.length) return false;
        for (const row of metricsByResult) {
            for (const k of metricKeys) {
                const v = toMetricNumber(row.metrics?.[k]);
                if (Number.isFinite(v)) return true;
            }
        }
        return false;
    })();

    // ---- Combined SVG layout (top metrics + grid below) ----
    $: metricsChartHeight = 200; // Height for 4-row bar chart
    $: metricsChartPadding = { top: 8, right: 10, bottom: 8, left: 60 }; // Left space for metric labels
    $: showTopMetrics = showMetricsChart && hasAnyMetrics;
    // Small internal left padding so the first radar column isn't clipped by SVG border
    const RADAR_PADDING_LEFT = 8;
    $: gridX0 = RADAR_PADDING_LEFT;
    $: actualGridWidth = gridWidth;
    $: actualGridHeight = gridHeight;
    $: metricsY0 = 0; // Metrics chart at the top
    $: gridY0 = showTopMetrics ? metricsChartHeight + 4 : 0; // Small gap between metrics and grid for visual separation
    $: totalWidth = rowHeaderWidth + RADAR_PADDING_LEFT + actualGridWidth; // Account for left padding
    $: totalHeight = (showTopMetrics ? metricsChartHeight + 4 : 0) + actualGridHeight;

    // Precompute column centers as reactive array so Svelte tracks sankeyDimensions dependency
    $: colCenters = Array.from({length: resultCount}, (_, ri) => {
        if (sankeyDimensions?.rowPositions?.length > ri) {
            return sankeyDimensions.rowPositions[ri];
        }
        return Math.round((ri * (cellSize + cellGap) + cellSize / 2) * 2) / 2;
    });

    // Precompute row centers as reactive array
    $: rowCenters = Array.from({length: labelCount}, (_, li) => {
        return Math.round((li * (cellSize + cellGap) + cellSize / 2) * 2) / 2;
    });

    function getCircleCenterY(li) {
        return rowCenters[li] ?? Math.round((li * (cellSize + cellGap) + cellSize / 2) * 2) / 2;
    }

    function getCircleCenterX(ri) {
        return colCenters[ri] ?? Math.round((ri * (cellSize + cellGap) + cellSize / 2) * 2) / 2;
    }

    function getCirclePosition(ri, li) {
        return { cx: colCenters[ri] ?? getCircleCenterX(ri), cy: rowCenters[li] ?? getCircleCenterY(li) };
    }


    // Bar chart data: one row per metric, bars aligned with grid columns below (colCenters)
    $: barChartData = (() => {
        if (!resultCount || !hasAnyMetrics) return { rows: [], chartWidth: 0, chartHeight: 0, barWidth: 0 };

        const chartWidth = Math.max(100, actualGridWidth - metricsChartPadding.left - metricsChartPadding.right);
        const chartHeight = metricsChartHeight - metricsChartPadding.top - metricsChartPadding.bottom;
        const rowCount = metricKeys.length;
        const rowGap = 4;
        const rowHeight = Math.max(20, (chartHeight - (rowCount - 1) * rowGap) / rowCount);
        const barMaxHeight = rowHeight - 4;

        const minColSpacing = resultCount > 1
            ? Math.min(...Array.from({ length: resultCount - 1 }, (_, i) =>
                Math.abs((colCenters[i + 1] ?? 0) - (colCenters[i] ?? 0))))
            : 40;
        const barWidth = Math.min(24, minColSpacing * 0.55);

        const rows = metricKeys.map((k, ki) => {
            const range = metricRanges[k] || { min: 0, max: 1 };
            const rowY = ki * (rowHeight + rowGap);
            const span = range.max - range.min;
            const safeSpan = span > 0 ? span : 1;

            const bars = [];
            for (let ri = 0; ri < resultCount; ri++) {
                const m = metricsByResult[ri]?.metrics || {};
                const rawValue = m[k];
                const value = toMetricNumber(rawValue);

                if (rawValue !== null && rawValue !== undefined && Number.isFinite(value)) {
                    const circleX = getCircleCenterX(ri);
                    const barCenterX = circleX - metricsChartPadding.left;
                    const x = barCenterX - barWidth / 2;

                    const normalized = clamp((value - range.min) / safeSpan, 0, 1);
                    const barH = Math.max(3, normalized * barMaxHeight);

                    bars.push({
                        x,
                        y: rowY + (barMaxHeight - barH) + 2,
                        width: barWidth,
                        height: barH,
                        value,
                        resultIndex: ri,
                        metricKey: k,
                        resultLabel: summaries?.[ri]?.result
                            ? getResultLabel(summaries[ri])
                            : `Result ${ri + 1}`,
                    });
                }
            }

            return {
                key: k,
                label: metricLabels[k] || k,
                bars,
                range,
                rowY,
                rowHeight,
            };
        });

        return { rows, chartWidth, chartHeight, barWidth };
    })();


    $: spotCount = spotOrder.length;
    $: spotSampleStep =
        spotCount > 0 ? Math.max(1, Math.ceil(spotCount / Math.max(1, maxSpotsPerCell))) : 1;
    $: spotLabelStep =
        spotCount > 0
            ? Math.max(
                  spotSampleStep,
                  Math.ceil(spotCount / Math.max(1, maxSpotLabelsPerCell)),
              )
            : 1;

    function toggleCollapse() {
        isCollapsed = !isCollapsed;
    }
</script>

<div class="mt-2 border-t border-slate-200 pt-2">
    <div
        class="flex flex-col flex-shrink-0"
        style={isCollapsed
            ? `height: ${COLLAPSED_HEIGHT}px; min-height: ${COLLAPSED_HEIGHT}px;`
            : "max-height: 52vh; min-height: 0;"}
    >
        {#if !isCollapsed}
            <div class="flex items-center justify-between mb-2 px-1">
                <h3 class="text-[11px] font-semibold uppercase tracking-wide text-slate-600">
                    {title}
                </h3>
                <div class="flex items-center gap-3">
                    <!-- <div class="text-[11px] text-slate-500">
                        {labelCount} labels · {resultCount} results · {spotCount} spots
                    </div> -->
                    <button
                        type="button"
                        class="flex items-center justify-center h-7 w-7 border border-slate-300 rounded-full bg-white text-gray-600 shadow-sm hover:bg-slate-100 transition-colors focus:outline-none focus:ring-2 focus:ring-slate-400"
                        on:click={toggleCollapse}
                        aria-label="Collapse"
                        title="Collapse"
                    >
                        <ChevronDown size={14} />
                    </button>
                </div>
            </div>
        {/if}

        <div
            class="border border-slate-200 rounded bg-white relative overflow-hidden"
            style={isCollapsed
                ? `height: ${COLLAPSED_HEIGHT}px; min-height: ${COLLAPSED_HEIGHT}px; overflow: hidden;`
                : "height: calc(52vh - 40px); min-height: 380px;"}
            bind:this={containerEl}
        >
        {#if isCollapsed}
            <div class="flex items-center justify-center h-full gap-3 px-4">
                <span class="text-[11px] font-semibold uppercase tracking-wide text-slate-600">
                    {title}
                </span>
                <button
                    type="button"
                    class="flex items-center justify-center h-8 w-8 border border-slate-300 rounded-full bg-white text-gray-600 shadow-sm hover:bg-slate-100 transition-colors focus:outline-none focus:ring-2 focus:ring-slate-400"
                    on:click={toggleCollapse}
                    aria-label="Expand"
                    title="Expand"
                >
                    <ChevronUp size={16} />
                </button>
            </div>
        {:else if !summaries.length || !labelOrder.length || !spotOrder.length}
            <div class="h-full w-full flex items-center justify-center text-sm text-slate-500">
                No comparison data.
            </div>
        {:else}
            <div class="absolute inset-0 flex flex-row">
                <div class="flex-shrink-0 overflow-auto py-4 pr-4">
                    <svg
                        bind:this={mainSvgEl}
                        width={totalWidth}
                        height={totalHeight}
                        viewBox={`0 0 ${totalWidth} ${totalHeight}`}
                        preserveAspectRatio="xMinYMin meet"
                        class="select-none"
                        style="display: block;"
                        role="application"
                        aria-label="Label-result grid with top metrics"
                    >
                        <defs>
                            <!-- Fill patterns for bar charts -->
                            <pattern id="pattern-diagonal" patternUnits="userSpaceOnUse" width="5" height="5" patternTransform="rotate(45)">
                                <line x1="0" y1="0" x2="0" y2="5" stroke="rgba(71,85,105,0.7)" stroke-width="1.2"/>
                            </pattern>
                            <pattern id="pattern-dots" patternUnits="userSpaceOnUse" width="5" height="5">
                                <circle cx="2.5" cy="2.5" r="1" fill="rgba(71,85,105,0.7)"/>
                            </pattern>
                        </defs>
                        {#if showTopMetrics && barChartData}
                            <g transform={`translate(${gridX0}, ${metricsY0})`}>
                                <rect
                                    x={0}
                                    y={0}
                                    width={actualGridWidth}
                                    height={metricsChartHeight}
                                    fill="rgba(248,250,252,0.5)"
                                    stroke="rgba(148,163,184,0.15)"
                                    stroke-width="0.5"
                                />

                                <g transform={`translate(${metricsChartPadding.left}, ${metricsChartPadding.top})`}>
                                    {#each barChartData.rows as row, ri (row.key)}
                                        <!-- Baseline for bars (single separator per row) -->
                                        <line
                                            x1={-metricsChartPadding.left + 4}
                                            y1={row.rowY + row.rowHeight - 2}
                                            x2={barChartData.chartWidth}
                                            y2={row.rowY + row.rowHeight - 2}
                                            stroke="rgba(148,163,184,0.25)"
                                            stroke-width="0.5"
                                        />

                                        <!-- Bars -->
                                        {#each row.bars as bar (bar.resultIndex)}
                                            <rect
                                                role="img"
                                                aria-label="{row.label}: {bar.value.toFixed(2)}"
                                                x={bar.x}
                                                y={bar.y}
                                                width={bar.width}
                                                height={bar.height}
                                                fill={metricFillPatterns[row.key]}
                                                stroke={themeBlueGray}
                                                stroke-width="1"
                                                rx="1"
                                                style="cursor: pointer;"
                                                on:pointerenter={(e) => {
                                                    const m = metricsByResult?.[bar.resultIndex]?.metrics || {};
                                                    const allMetrics = {};
                                                    for (const k of metricKeys) {
                                                        const v = toMetricNumber(m[k]);
                                                        if (Number.isFinite(v)) {
                                                            allMetrics[k] = {
                                                                label: metricLabels[k] || k,
                                                                value: v,
                                                                color: metricStrokeColors[k],
                                                            };
                                                        }
                                                    }
                                                    metricsHover = {
                                                        x: e.clientX,
                                                        y: e.clientY,
                                                        resultIndex: bar.resultIndex,
                                                        resultLabel: bar.resultLabel,
                                                        metricKey: row.key,
                                                        metricLabel: row.label,
                                                        value: bar.value,
                                                        displayValue: bar.value,
                                                        color: themeBlueGray,
                                                        allMetrics,
                                                    };
                                                }}
                                                on:pointermove={(e) => {
                                                    if (metricsHover) {
                                                        metricsHover = { ...metricsHover, x: e.clientX, y: e.clientY };
                                                    }
                                                }}
                                                on:pointerleave={handleMetricsPointerLeave}
                                            />

                                            <!-- Value label on top of bar -->
                                            {#if bar.height > 8}
                                                <text
                                                    x={bar.x + bar.width / 2}
                                                    y={bar.y - 2}
                                                    text-anchor="middle"
                                                    dominant-baseline="auto"
                                                    font-size="7"
                                                    fill="rgba(71,85,105,0.6)"
                                                >
                                                    {bar.value.toFixed(2)}
                                                </text>
                                            {/if}
                                        {/each}
                                    {/each}
                                </g>
                            </g>
                        {/if}

                        <g transform={`translate(${gridX0}, ${gridY0})`}>

                    <!-- Grid cells: each cell is a circle with spots distributed on its circumference -->
                    {#each summaries as summary, ri (stableIdForSummary(summary, ri))}
                        {#each labelOrder as label, li (label)}
                            {@const map = barcodeMaps[ri]}
                            {@const resultId = summary?.result?.cluster_result_id}
                            {@const resultLabel = summary?.result
                                ? (summary.result.result_name || summary.result.cluster_result_id || `Result ${ri + 1}`)
                                : `Result ${ri + 1}`}
                            {@const isSelected = selectedResultId && selectedResultId !== "default"
                                ? resultId === selectedResultId
                                : false}
                            
                            {@const cx = colCenters[ri] ?? 0}
                            {@const cy = rowCenters[li] ?? 0}
                            
                            {@const clusterColor = getClusterColor(summary, label) || "rgba(148,163,184,0.8)"}
                            {@const cellStroke = isSelected ? "rgba(37,99,235,0.55)" : "rgba(148,163,184,0.55)"}
                            {@const labelFont = clamp(cellRadius * 0.22, 6, 9)}

                            <g>
                                <!-- Cluster cell circle -->
                                <circle
                                    cx={cx}
                                    cy={cy}
                                    r={cellRadius}
                                    fill="rgba(255,255,255,0.75)"
                                    stroke={cellStroke}
                                    stroke-width={isSelected ? 1.4 : 1}
                                    vector-effect="non-scaling-stroke"
                                />

                                <!-- Radar chart for cluster metrics (inside the circle) -->
                                {#if getClusterMetrics(resultId, label)}
                                    {@const clusterMetrics = getClusterMetrics(resultId, label)}
                                    {@const radarRadius = cellRadius * 0.75}
                                    {@const numAxes = clusterMetricKeys.length}
                                    {@const angleStep = TAU / numAxes}
                                    
                                    <!-- Grid circles -->
                                    <g opacity="0.15">
                                        {#each [0.25, 0.5, 0.75, 1] as level}
                                            <circle
                                                cx={cx}
                                                cy={cy}
                                                r={radarRadius * level}
                                                fill="none"
                                                stroke="rgba(71,85,105,0.4)"
                                                stroke-width="0.5"
                                            />
                                        {/each}
                                    </g>
                                    
                                    <!-- Grid axes -->
                                    <g opacity="0.2">
                                        {#each clusterMetricKeys as mk, i (mk)}
                                            {@const angle = i * angleStep - Math.PI / 2}
                                            <line
                                                x1={cx}
                                                y1={cy}
                                                x2={cx + Math.cos(angle) * radarRadius}
                                                y2={cy + Math.sin(angle) * radarRadius}
                                                stroke="rgba(71,85,105,0.4)"
                                                stroke-width="0.5"
                                            />
                                        {/each}
                                    </g>
                                    
                                    <!-- Radar area (filled polygon) -->
                                    {@const radarPoints = (() => {
                                        const points = [];
                                        clusterMetricKeys.forEach((mk, i) => {
                                            const val = toMetricNumber(clusterMetrics[mk]);
                                            const normalized = normalizeMetricValue(val, mk);
                                            const angle = i * angleStep - Math.PI / 2;
                                            const r = radarRadius * normalized;
                                            points.push({
                                                x: cx + Math.cos(angle) * r,
                                                y: cy + Math.sin(angle) * r,
                                            });
                                        });
                                        return points;
                                    })()}
                                    
                                    {#if radarPoints.length > 0}
                                        {@const radarPath = generateSmoothRadarPath(radarPoints, true)}
                                        
                                        <path
                                            role="img"
                                            aria-label="Cluster {label} metrics: Silhouette {clusterMetrics?.silhouette ?? '—'}, Moran's I {clusterMetrics?.morans_i ?? '—'}, Geary's C {clusterMetrics?.gearys_c ?? '—'}"
                                            d={radarPath}
                                            fill="rgba(100, 116, 139, 0.2)"
                                            stroke="rgba(100, 116, 139, 0.8)"
                                            stroke-width="1"
                                            vector-effect="non-scaling-stroke"
                                            style="cursor: pointer;"
                                            on:pointerenter={(e) => handleRadarEnter(e, {
                                                resultId,
                                                resultLabel,
                                                label,
                                                clusterLabel: label,
                                                metrics: clusterMetrics,
                                            })}
                                            on:pointermove={handleRadarMove}
                                            on:pointerleave={handleRadarLeave}
                                        />
                                    {/if}
                                {/if}

                                <!-- Spots on this cluster cell (only if barcode's cluster equals this label) -->
                                {#each spotOrder as barcode, si (barcode)}
                                    {#if si % spotSampleStep === 0}
                                        {@const c = map.get(barcode)}
                                        {#if c && `${c}`.trim() === `${label}`.trim()}
                                            {@const phi = (si / spotCount) * TAU - Math.PI / 2}
                                            {@const innerR = cellRadius * spotRadialLineInnerRatio}
                                            {@const px = cx + Math.cos(phi) * cellRadius}
                                            {@const py = cy + Math.sin(phi) * cellRadius}
                                            {@const pxInner = cx + Math.cos(phi) * innerR}
                                            {@const pyInner = cy + Math.sin(phi) * innerR}
                                            {@const spotLabel = formatSpotLabel(barcode, {
                                                spotIndex: si,
                                                resultIndex: ri,
                                                label,
                                                summary,
                                            })}

                                            <!-- spot radial line -->
                                            <line
                                                role="img"
                                                aria-label={spotLabel ? `Spot ${spotLabel} in ${label}` : `Spot in cluster ${label}`}
                                                x1={pxInner}
                                                y1={pyInner}
                                                x2={px}
                                                y2={py}
                                                stroke={clusterColor}
                                                stroke-width={spotRadialLineWidth}
                                                stroke-linecap="round"
                                                style="cursor: default;"
                                                on:pointerenter={(e) =>
                                                    handleSpotEnter(e, {
                                                        barcode,
                                                        spotLabel,
                                                        label,
                                                        resultLabel: getResultLabel(summary),
                                                        color: clusterColor,
                                                    })}
                                                on:pointermove={handleSpotMove}
                                                on:pointerleave={handleSpotLeave}
                                            />

                                            {#if showSpotLabels && spotLabel && si % spotLabelStep === 0}
                                                <!-- place text along the tangent (perpendicular to vector from cell center to spot) -->
                                                {@const ga = Math.atan2(py - cy, px - cx)}
                                                {@const tangent = ga + Math.PI / 2}
                                                {@const rotInfo = textRotationForTangent(tangent)}
                                                {@const tx = px + Math.cos(tangent) * tangentLabelOffset}
                                                {@const ty = py + Math.sin(tangent) * tangentLabelOffset}
                                                <text
                                                    x={tx}
                                                    y={ty}
                                                    font-size={labelFont}
                                                    fill="rgba(15,23,42,0.75)"
                                                    text-anchor={rotInfo.flip ? "end" : "start"}
                                                    dominant-baseline="middle"
                                                    transform={`rotate(${rotInfo.deg.toFixed(2)}, ${tx.toFixed(2)}, ${ty.toFixed(2)})`}
                                                >
                                                    {spotLabel}
                                                </text>
                                            {/if}
                                        {/if}
                                    {/if}
                                {/each}
                            </g>
                        {/each}
                    {/each}
                        </g>
                    </svg>
                </div>

                <!-- Legend panel (right): fill remaining width and full height of table area -->
                <div class="border-l border-slate-200 bg-slate-50/60 flex-1 min-w-0 h-full flex flex-col min-h-0">
                    <div class="flex-1 flex flex-col gap-3 min-h-0 overflow-y-auto px-4 py-3 text-[11px] text-slate-600">
                        <div class="text-[10px] font-semibold uppercase tracking-wider text-slate-500">
                            Legend
                        </div>

                        <!-- Grid encoding -->
                        <!-- <section class="space-y-1">
                            <h4 class="text-[10px] font-semibold text-slate-600 mb-0.5">Grid</h4>
                            <div class="grid grid-cols-2 gap-x-3 gap-y-0.5 text-[10px]">
                                <div><span class="font-medium text-slate-700">Row</span> <span class="text-slate-400">→</span> label</div>
                                <div><span class="font-medium text-slate-700">Column</span> <span class="text-slate-400">→</span> result</div>
                                <div><span class="font-medium text-slate-700">Cell</span> <span class="text-slate-400">→</span> cluster circle</div>
                                <div><span class="font-medium text-slate-700">Radial</span> <span class="text-slate-400">→</span> spots</div>
                            </div>
                        </section> -->

                        <!-- Result-level metrics (bar chart) -->
                        {#if showMetricsChart && hasAnyMetrics && barChartData}
                            <section class=" border-slate-200/60 space-y-1.5">
                                <!-- <h4 class="text-[10px] font-semibold text-slate-600 mb-0.5">Result metrics</h4> -->
                                <ul class="flex flex-col gap-1">
                                    {#each metricKeys as mk (mk)}
                                        <li class="flex gap-2">
                                            <svg width="12" height="12" class="flex-shrink-0 mt-0.5" aria-hidden="true">
                                                <defs>
                                                    <pattern id="legend-diagonal-{mk}" patternUnits="userSpaceOnUse" width="4" height="4" patternTransform="rotate(45)">
                                                        <line x1="0" y1="0" x2="0" y2="4" stroke="rgba(71,85,105,0.7)" stroke-width="1"/>
                                                    </pattern>
                                                    <pattern id="legend-dots-{mk}" patternUnits="userSpaceOnUse" width="4" height="4">
                                                        <circle cx="2" cy="2" r="0.8" fill="rgba(71,85,105,0.7)"/>
                                                    </pattern>
                                                </defs>
                                                <rect
                                                    x="0.5" y="0.5" width="11" height="11" rx="1"
                                                    fill={mk === "chao" ? "white" : mk === "silhouette" ? themeBlueGrayLight : mk === "pas" ? `url(#legend-diagonal-${mk})` : `url(#legend-dots-${mk})`}
                                                    stroke={themeBlueGray}
                                                    stroke-width="1"
                                                />
                                            </svg>
                                            <div class="min-w-0 flex-1">
                                                <div class="font-medium text-slate-700">{metricLabels[mk]}</div>
                                                <div class="text-[9px] text-slate-500 leading-snug mt-0.5">{metricDescriptions[mk]}</div>
                                            </div>
                                        </li>
                                    {/each}
                                </ul>
                            </section>
                        {/if}

                        <!-- Radar (cluster-level metrics) -->
                        {#if clusterMetricsData && clusterMetricsData.size > 0}
                            {@const radSize = 70}
                            {@const radPadSide = 48}
                            {@const radPadTop = 18}
                            {@const radPadBottom = 0}
                            {@const radL = radSize + radPadSide * 2}
                            {@const radH = radSize + radPadTop + radPadBottom}
                            {@const radCx = radSize / 2 + radPadSide}
                            {@const radCy = radSize / 2 + radPadTop}
                            {@const radR = radSize * 0.35}
                            {@const radStep = TAU / clusterMetricKeys.length}
                            {@const radLegPoints = (() => {
                                const pts = [];
                                clusterMetricKeys.forEach((_, i) => {
                                    const a = i * radStep - Math.PI / 2;
                                    const n = [0.75, 0.5, 0.85][i] ?? 0.7;
                                    pts.push({ x: radCx + Math.cos(a) * radR * n, y: radCy + Math.sin(a) * radR * n });
                                });
                                return pts;
                            })()}
                            {@const radLegPath = radLegPoints.length >= 2 ? generateSmoothRadarPath(radLegPoints, true) : ""}
                            <section class="pt-2 border-t border-slate-200/60">
                                <!-- <h4 class="text-[10px] font-semibold text-slate-600 mb-1.5">Cluster metrics (radar)</h4> -->
                                <div class="flex flex-col gap-2">
                                    <div class="flex-shrink-0 flex justify-center" style="width: 100%; min-height: {radH}px;">
                                        <svg width={radL} height={radH} viewBox={`0 0 ${radL} ${radH}`} class="text-slate-500">
                                            <g opacity="0.3">
                                                {#each [0.33, 0.66, 1] as lv}
                                                    <circle cx={radCx} cy={radCy} r={radR * lv} fill="none" stroke="currentColor" stroke-width="1"/>
                                                {/each}
                                            </g>
                                            <g opacity="0.5">
                                                {#each clusterMetricKeys as mk, i (mk)}
                                                    {@const a = i * radStep - Math.PI / 2}
                                                    <line x1={radCx} y1={radCy} x2={radCx + Math.cos(a) * radR} y2={radCy + Math.sin(a) * radR} stroke="currentColor" stroke-width="1"/>
                                                {/each}
                                            </g>
                                            {#if radLegPath}
                                                <path d={radLegPath} fill="rgba(100,116,139,0.2)" stroke="rgba(100,116,139,0.8)" stroke-width="1.5"/>
                                            {/if}
                                            {#each clusterMetricKeys as mk, i (mk)}
                                                {@const a = i * radStep - Math.PI / 2}
                                                {@const labelR = radR + 8}
                                                {@const tx = radCx + Math.cos(a) * labelR}
                                                {@const ty = radCy + Math.sin(a) * labelR}
                                                {@const anchor = Math.abs(Math.cos(a)) < 0.2 ? "middle" : (Math.cos(a) > 0 ? "start" : "end")}
                                                {@const base = Math.abs(Math.sin(a)) < 0.2 ? "middle" : (Math.sin(a) > 0 ? "hanging" : "baseline")}
                                                {@const dx = Math.cos(a) * 2}
                                                {@const dy = Math.sin(a) * 2}
                                                <text x={tx} y={ty} dx={dx} dy={dy} text-anchor={anchor} dominant-baseline={base} font-size="10" font-weight="600" fill="#334155">{clusterMetricLabels[mk]}</text>
                                            {/each}
                                        </svg>
                                    </div>
                                    <ul class="flex flex-col gap-1.5 min-w-0">
                                        {#each clusterMetricKeys as mk}
                                            <li class="flex flex-col gap-0.5">
                                                <div class="font-medium text-slate-700 text-[10px]">{clusterMetricLabels[mk]}</div>
                                                <div class="text-[9px] text-slate-500 leading-snug">{clusterMetricDescriptions[mk]}</div>
                                            </li>
                                        {/each}
                                    </ul>
                                </div>
                            </section>
                        {/if}
                    </div>
                </div>
            </div>

            {#if hover}
                <div
                    class="pointer-events-none fixed z-50 max-w-[320px] rounded-md bg-slate-900/95 px-3 py-2 text-[11px] text-white shadow-lg backdrop-blur-sm"
                    style={`left: ${Math.round(hover.x + 12)}px; top: ${Math.round(hover.y + 12)}px;`}
                >
                    <div class="text-[12px] font-semibold mb-1">{hover.spotLabel || "Spot"}</div>
                    <div class="space-y-1 text-[10px] text-slate-200">
                        <div class="flex items-center gap-2">
                            <span class="inline-flex h-2 w-2 rounded-full" style={`background:${hover.color};`}></span>
                            <span class="truncate">
                                {hover.resultLabel} · <span class="font-medium">{hover.label}</span>
                            </span>
                        </div>
                        <div class="text-slate-300 truncate">Barcode: {hover.barcode}</div>
                    </div>
                </div>
            {/if}

            {#if metricsHover}
                <div
                    class="pointer-events-none fixed z-50 max-w-[320px] rounded-md bg-slate-900/95 px-3 py-2 text-[11px] text-white shadow-lg backdrop-blur-sm"
                    style={`left: ${Math.round(metricsHover.x + 16)}px; top: ${Math.round(metricsHover.y - 8)}px;`}
                >
                    <svg width="0" height="0" aria-hidden="true">
                        <defs>
                            <pattern id="tt-pattern-diagonal" patternUnits="userSpaceOnUse" width="4" height="4" patternTransform="rotate(45)">
                                <line x1="0" y1="0" x2="0" y2="4" stroke="rgba(255,255,255,0.8)" stroke-width="1"/>
                            </pattern>
                            <pattern id="tt-pattern-dots" patternUnits="userSpaceOnUse" width="4" height="4">
                                <circle cx="2" cy="2" r="0.8" fill="rgba(255,255,255,0.8)"/>
                            </pattern>
                        </defs>
                    </svg>
                    <div class="text-[12px] font-semibold mb-1">{metricsHover.resultLabel}</div>
                    <div class="space-y-1 text-[10px] text-slate-200">
                        {#if metricsHover.allMetrics}
                            {#each metricKeys as mk (mk)}
                                {@const metric = metricsHover.allMetrics[mk]}
                                {#if metric}
                                    <div class="flex items-center gap-2">
                                        <svg width="12" height="12" class="flex-shrink-0">
                                            <rect
                                                x="1" y="1" width="10" height="10" rx="1"
                                                fill={mk === "chao" ? "transparent" : mk === "silhouette" ? "rgba(255,255,255,0.6)" : mk === "pas" ? "url(#tt-pattern-diagonal)" : "url(#tt-pattern-dots)"}
                                                stroke="rgba(255,255,255,0.9)"
                                                stroke-width="1"
                                            />
                                        </svg>
                                        <span class="truncate">
                                            {metric.label}: <span class="font-medium">{metric.value.toFixed(4)}</span>
                                        </span>
                                    </div>
                                {/if}
                            {/each}
                        {:else}
                            <div class="flex items-center gap-2">
                                <svg width="12" height="12" class="flex-shrink-0">
                                    <rect
                                        x="1" y="1" width="10" height="10" rx="1"
                                        fill={metricsHover.metricKey === "chao" ? "transparent" : metricsHover.metricKey === "silhouette" ? "rgba(255,255,255,0.6)" : metricsHover.metricKey === "pas" ? "url(#tt-pattern-diagonal)" : "url(#tt-pattern-dots)"}
                                        stroke="rgba(255,255,255,0.9)"
                                        stroke-width="1"
                                    />
                                </svg>
                                <span class="truncate">
                                    {metricsHover.metricLabel}: <span class="font-medium">{metricsHover.displayValue.toFixed(4)}</span>
                                </span>
                            </div>
                        {/if}
                    </div>
                </div>
            {/if}

            {#if radarHover}
                <div
                    class="pointer-events-none fixed z-50 max-w-[320px] rounded-md bg-slate-900/95 px-3 py-2 text-[11px] text-white shadow-lg backdrop-blur-sm"
                    style={`left: ${Math.round(radarHover.x + 12)}px; top: ${Math.round(radarHover.y + 12)}px;`}
                >
                    <div class="text-[12px] font-semibold mb-1">
                        {radarHover.resultLabel} · Cluster {radarHover.clusterLabel}
                    </div>
                    <div class="space-y-1 text-[10px] text-slate-200">
                        {#if radarHover.metrics?.silhouette !== null && radarHover.metrics?.silhouette !== undefined}
                            <div>
                                <span class="font-medium">Silhouette:</span> {toMetricNumber(radarHover.metrics.silhouette).toFixed(4)}
                            </div>
                        {/if}
                        {#if radarHover.metrics?.morans_i !== null && radarHover.metrics?.morans_i !== undefined}
                            <div>
                                <span class="font-medium">Moran's I:</span> {toMetricNumber(radarHover.metrics.morans_i).toFixed(4)}
                            </div>
                        {/if}
                        {#if radarHover.metrics?.gearys_c !== null && radarHover.metrics?.gearys_c !== undefined}
                            <div>
                                <span class="font-medium">Geary's C:</span> {toMetricNumber(radarHover.metrics.gearys_c).toFixed(4)}
                            </div>
                        {/if}
                    </div>
                </div>
            {/if}
        {/if}
        </div>
    </div>
</div>


