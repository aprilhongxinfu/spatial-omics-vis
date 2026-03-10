<script>
    import { afterUpdate, onDestroy, onMount, tick } from "svelte";
    import Plotly from "plotly.js-dist-min";
    import { createEventDispatcher } from "svelte";
    import { Info } from "@lucide/svelte";
    import LabelResultClusterSpotCircular from "./labelResultClusterSpotCircular.svelte";
    import SankeyDiagramVertical from "./sankeyDiagramVertical.svelte";

    export let clusterResults = []; // Visible results (for display order)
    export let allClusterResults = []; // All results including hidden ones (for data loading)
    export let hiddenResultIds = new Set(); // IDs of hidden results
    export let baseApi = "";
    export let currentSlice = "";
    // 使用与 Plot 组件相同的数据和图像，保证对齐
    export let spatialData = [];
    export let image = null;
    export let scrollSource = null;
    export let verticalLayout = false; // 是否使用纵向布局（每行一个聚类结果）
    // Currently selected result (used to highlight a row in Sankey & metrics table)
    export let selectedResultId = null;
    export let isLeftPanelCollapsed = false; // 监听左侧栏收起状态，用于触发右侧自适应

    const dispatch = createEventDispatcher();

    let loading = false;
    let error = null;
    let distributionSummary = []; // All loaded summaries including hidden
    let legendEntries = [];
    const colorCache = new Map();
    
    // Filter distributionSummary to only show visible results
    $: visibleDistributionSummary = distributionSummary.filter(
        summary => !hiddenResultIds.has(summary.result?.cluster_result_id)
    );

    let comparisonKey = "";
    let requestToken = 0;
    // Sankey diagram variables moved to sankeyDiagram.svelte component

    // Row-local focus: double-click a cluster bar to expand that cluster within that row only.
    // Shape: { [rowIndex: number]: clusterName }
    let focusedClusterByRow = {};
    // The "active" focus used for connected filtering in Individual mode.
    // Shape: { rowIndex: number, clusterName: string } | null
    let activeFocus = null;
    // Frozen barcode rank captured at the time of focusing (for stable vertical alignment in focused Individual mode)
    // Map<barcode, rank> | null
    let activeFocusRank = null;
    // Cluster layout info from Sankey for alignment
    // Map<rowIndex, Map<clusterName, { x, width }>>
    let clusterLayoutInfo = new Map();
    // Sankey dimensions for alignment
    let sankeyDimensions = { paddingLeft: 24, sankeyWidth: 0, totalWidth: 0, screenWidth: 0, rowPositions: [], rowHeight: 0, rowSpacing: 0 };
    // tooltipPosition moved to sankeyDiagram.svelte component
    // barcode highlighted from spatial preview hover (for syncing to Sankey)
    let highlightedBarcodeFromPreview = null;
    // barcode highlighted from Sankey hover (for highlighting connection line)
    let highlightedBarcodeFromSankey = null;
    // Track currently highlighted barcode in spatial preview to avoid redundant updates
    let currentHighlightedBarcode = null;
    let currentHighlightColor = null;
    // measureRaf, measureQueued, activeScrollSource, detachScrollSync, resizeObserver moved to sankeyDiagram.svelte component

    
    // Metrics visualization - now drawn on the same canvas as Sankey
    let metricsData = new Map(); // cluster_result_id -> metrics
    let metricsPointsCache = new Map(); // metric key -> points array
    
    // Cluster-level metrics for radar chart
    let clusterMetricsData = new Map(); // cluster_result_id -> { cluster -> metrics }

    // Spatial preview (slice-level scatter plot) on the right of Sankey - using Plotly
    let spatialPreviewDiv;
    let spatialPreviewPlotInstance = null;
    // barcode -> { xs: number[], ys: number[] } for highlight trace
    let spatialPreviewBarcodePoints = new Map();
    let spatialPreviewResizeObserver = null;
    let spatialPreviewEventsBound = false; // Track if events are already bound
    // 防抖：避免短时间内多次调用 drawSpatialPreviewPlot
    let drawSpatialPreviewTimeout = null;
    let drawAllPieChartsTimeout = null;
    
    // Pie chart overlay for showing cluster distribution
    let pieChartCanvas;
    // barcode -> { cluster -> count, color } for pie chart
    let barcodeClusterDistribution = new Map();
    // barcode -> stability percent (0-100)
    let barcodeStabilityPercent = new Map();
    // Currently hovered barcode for pie chart display
    let pieChartBarcode = null;
    let pieChartPosition = { x: 0, y: 0 };
    // Multi-barcode hover pie charts (e.g. hovering a cluster/trend in Sankey)
    let pieChartMultiBarcodes = null; // string[] | null
    let pieChartMultiRaf = 0;
    let pieChartMultiPending = null;
    // 当前在"显示所有饼图"模式下高亮的 barcode
    let allPieChartsHighlightBarcode = null;
    
    // Safety cap for performance (counts barcode-locations, not unique barcodes)
    const MAX_HOVER_PIES = 1500;

    /** 
     * Plotly 的 hoverlayer 是 SVG 内的 <g> 元素，不支持 CSS z-index。
     * 因此无法通过 z-index 让 tooltip 显示在 canvas 之上。
     * 解决方案：在 hover 时高亮对应饼图，其他降低透明度
     */
    function ensureTooltipAbovePieCanvas() {
        // 保留此函数以兼容现有调用
    }

    /** 根据容器大小、下面是否收缩计算饼图半径（统一逻辑，不区分 showAllPieCharts） */
    function calculatePieChartRadius() {
        if (!spatialPreviewDiv || !image) return { radius: 2.5, highlightRadius: 2.5 };
        const rect = spatialPreviewDiv.getBoundingClientRect();
        const scaleX = rect.width / image.width;
        const scaleY = rect.height / image.height;
        const scale = Math.min(scaleX, scaleY);
        const spotScale = 2.5 * scale * 1.2; // 与 Plotly spot 半径相近的基准
        const spotSame = spotScale * 0.8;    // 没收缩时用于「普通」饼图，略小于 spot
        // 统一半径计算：无论 showAllPieCharts 开关如何，饼图大小一致
        const baseRadius = isBelowCollapsed
            ? Math.max(3, Math.min(12, spotScale * 2.5))   // 收缩后明显大
            : Math.max(1.2, Math.min(3.5, spotSame));     // 没收缩与 spot 一样大
        const highlightRadius = baseRadius * 1.5;
        return { radius: baseRadius, highlightRadius };
    }

    // 下面 Circular comparison 区域是否收缩（来自 LabelResultClusterSpotCircular，用于饼图大小）
    let isBelowCollapsed = false;

    // Whether to show the bottom cluster metrics/radar tables (now rendered via MetricsComparison).
    let showClusterTables = true;

    // When the below section collapses/expands (toggled inside LabelResultClusterSpotCircular),
    // force a Plotly redraw so the spatial preview stays aligned and crisp.
    let previousBelowCollapsed = isBelowCollapsed;
    $: if (isBelowCollapsed !== previousBelowCollapsed) {
        previousBelowCollapsed = isBelowCollapsed;
        // Wait for DOM to update after state change，使用防抖重绘空间图与饼图
        tick().then(() => {
            if (spatialPreviewDiv && spatialData && spatialData.length && image) {
                debouncedDrawSpatialPreviewPlot(150);
                if (showAllPieCharts && pieChartCanvas) {
                    debouncedDrawAllPieCharts(allPieChartsHighlightBarcode, 200);
                }
            }
        });
    }
    
    // Filter spots by stability percentage (0-100).
    // Stability(barcode) = max(cluster_count_across_results) / num_results * 100.
    // Barcodes with stability > maxStabilityPercent will be filtered out.
    let maxStabilityPercent = 100;
    let showMaxStabilityTooltip = false;
    let showAllPieChartsTooltip = false;
    let showClusterComparisonTooltip = false;
    
    // Show pie charts for all spots
    let showAllPieCharts = false;
    
    // How to draw flow connections between rows:
    // - "aggregated": cluster->cluster thick links (cleaner)
    // - "individual": one link per barcode (trackable)
    let flowMode = "individual";
    
    // How to segment/order spots inside each cluster bar:
    // - "cluster_only": no extra segmentation; order spots within each cluster by FIRST row's order
    // - "next_cluster": group by next-row cluster and order by distance to current cluster (left/center/right)
    // - "cluster_set": group by the set of clusters a barcode ever touches (e.g. {1,2,3})
    // - "trend_sequence": group by the full per-result sequence (more granular, can get noisy)
    let segmentMode = "next_cluster";
    
    // Calculate trend for a barcode across all cluster results
    // Returns a trend signature string representing the cluster sequence
    function calculateBarcodeTrend(barcode, summaries) {
        const clusterSequence = [];
        summaries.forEach((summary) => {
            const spot = summary.spots?.find(s => `${s.barcode}`.trim() === `${barcode}`.trim());
            if (spot) {
                clusterSequence.push(String(spot.cluster).trim());
            } else {
                clusterSequence.push(null);
            }
        });
        
        // Create a trend signature: convert cluster names to a normalized pattern
        // For numeric clusters, we use numeric comparison; for text, use string
        const trendKey = clusterSequence.map((cluster, idx) => {
            if (cluster === null) return '?';
            const num = Number.parseFloat(cluster);
            if (!Number.isNaN(num)) {
                // Normalize numeric clusters: calculate relative position
                const allNumeric = clusterSequence.filter(c => c !== null).map(c => {
                    const n = Number.parseFloat(c);
                    return Number.isNaN(n) ? null : n;
                }).filter(n => n !== null);
                if (allNumeric.length > 0) {
                    const min = Math.min(...allNumeric);
                    const max = Math.max(...allNumeric);
                    const range = max - min;
                    if (range > 0) {
                        const normalized = (num - min) / range;
                        return normalized.toFixed(2);
                    }
                }
                return num.toString();
            }
            return cluster;
        }).join(',');
        
        return { sequence: clusterSequence, key: trendKey };
    }

    function buildClusterSetFromTrendSequence(sequence) {
        const items = (Array.isArray(sequence) ? sequence : [])
            .map((c) => (c === null || c === undefined ? null : String(c).trim()))
            .filter((c) => c && c !== "?");
        // unique + sorted for stable key
        const uniq = Array.from(new Set(items));
        uniq.sort((a, b) => {
            const na = Number.parseFloat(a);
            const nb = Number.parseFloat(b);
            const bothNumeric = !Number.isNaN(na) && !Number.isNaN(nb);
            return bothNumeric
                ? na - nb
                : String(a).localeCompare(String(b), undefined, { numeric: true });
        });
        return uniq;
    }

    function clusterSetKeyFromSet(clusterSet) {
        const arr = Array.isArray(clusterSet) ? clusterSet : [];
        return arr.join("|");
    }

    function jaccardSimilarity(setA, setB) {
        const a = new Set(Array.isArray(setA) ? setA : []);
        const b = new Set(Array.isArray(setB) ? setB : []);
        if (a.size === 0 && b.size === 0) return 1;
        let inter = 0;
        for (const v of a) if (b.has(v)) inter += 1;
        const union = a.size + b.size - inter;
        return union ? inter / union : 0;
    }

    function sortClusterSetsBySimilarity(trendGroups) {
        const entries = Array.from(trendGroups.entries()).map(([key, group]) => ({
            key,
            set: group.sequence || [],
        }));
        if (entries.length <= 1) return entries.map((e) => e.key);

        // Prefer smaller sets (more "stable") first, then cluster similar sets together
        entries.sort((a, b) => {
            const sa = a.set?.length ?? 0;
            const sb = b.set?.length ?? 0;
            if (sa !== sb) return sa - sb;
            return String(a.key).localeCompare(String(b.key));
        });

        const ordered = [entries[0]];
        const remaining = entries.slice(1);

        while (remaining.length) {
            const last = ordered[ordered.length - 1];
            let bestIdx = 0;
            let bestSim = -1;
            for (let i = 0; i < remaining.length; i++) {
                const sim = jaccardSimilarity(last.set, remaining[i].set);
                if (sim > bestSim) {
                    bestSim = sim;
                    bestIdx = i;
                }
            }
            ordered.push(remaining.splice(bestIdx, 1)[0]);
        }

        return ordered.map((e) => e.key);
    }

    const ADJ_STABLE_KEY = "__adj_stable__";

    function isAdjacentStableAtRow(rowVisible, rowIndex, barcode, clusterName) {
        const bc = barcode === null || barcode === undefined ? "" : `${barcode}`.trim();
        if (!bc) return false;
        const cName = clusterName === null || clusterName === undefined ? "" : `${clusterName}`.trim();
        if (!cName) return false;

        const hasPrev = rowIndex > 0;
        const hasNext = rowIndex < rowVisible.length - 1;
        if (!hasPrev && !hasNext) return false;

        const prevMap = hasPrev ? rowVisible[rowIndex - 1].barcodeClusterMap : null;
        const nextMap = hasNext ? rowVisible[rowIndex + 1].barcodeClusterMap : null;

        if (hasPrev) {
            const prevC = prevMap ? prevMap.get(bc) : null;
            if (`${prevC ?? ""}`.trim() !== cName) return false;
        }
        if (hasNext) {
            const nextC = nextMap ? nextMap.get(bc) : null;
            if (`${nextC ?? ""}`.trim() !== cName) return false;
        }
        return true;
    }

    function parseClusterNumber(value) {
        const s = value === null || value === undefined ? "" : String(value).trim();
        if (!s) return null;
        const n = Number.parseFloat(s);
        return Number.isNaN(n) ? null : n;
    }

    function compareClusterLabel(a, b) {
        const sa = a === null || a === undefined ? "" : String(a).trim();
        const sb = b === null || b === undefined ? "" : String(b).trim();
        const na = parseClusterNumber(sa);
        const nb = parseClusterNumber(sb);
        const bothNumeric = na !== null && nb !== null;
        return bothNumeric
            ? na - nb
            : sa.localeCompare(sb, undefined, { numeric: true });
    }

    // Compare destination clusters by how "far" they are from a given source cluster.
    // Primary: absolute numeric distance |to-from| (near first)
    // Secondary: direction (to < from first, then equal, then to > from)
    // Fallback (non-numeric): compare labels
    function compareClusterDistanceFrom(fromCluster, toA, toB) {
        const fromStr = fromCluster === null || fromCluster === undefined ? "" : String(fromCluster).trim();
        const aStr = toA === null || toA === undefined ? "" : String(toA).trim();
        const bStr = toB === null || toB === undefined ? "" : String(toB).trim();
        const fromNum = parseClusterNumber(fromStr);
        const aNum = parseClusterNumber(aStr);
        const bNum = parseClusterNumber(bStr);
        if (fromNum !== null && aNum !== null && bNum !== null) {
            const da = aNum - fromNum;
            const db = bNum - fromNum;
            const absCmp = Math.abs(da) - Math.abs(db);
            if (absCmp !== 0) return absCmp;
            const dirRank = (d) => (d < 0 ? 0 : d === 0 ? 1 : 2);
            const dirCmp = dirRank(da) - dirRank(db);
            if (dirCmp !== 0) return dirCmp;
            return da - db;
        }
        return compareClusterLabel(aStr, bStr);
    }

    function getNextClusterKey(rowVisible, rowIndex, barcode) {
        const bc = barcode === null || barcode === undefined ? "" : `${barcode}`.trim();
        if (!bc) return null;
        if (rowIndex >= rowVisible.length - 1) return "End";
        const nextMap = rowVisible[rowIndex + 1]?.barcodeClusterMap;
        const nextC = nextMap ? nextMap.get(bc) : null;
        return nextC === null || nextC === undefined || `${nextC}`.trim() === "" ? "Gone" : `${nextC}`.trim();
    }

    function getPrevClusterKey(rowVisible, rowIndex, barcode) {
        const bc = barcode === null || barcode === undefined ? "" : `${barcode}`.trim();
        if (!bc) return null;
        if (rowIndex <= 0) return "Start";
        const prevMap = rowVisible[rowIndex - 1]?.barcodeClusterMap;
        const prevC = prevMap ? prevMap.get(bc) : null;
        return prevC === null || prevC === undefined || `${prevC}`.trim() === "" ? "Gone" : `${prevC}`.trim();
    }

    // Order destination-cluster groups around the current cluster:
    // - same as current cluster goes to center
    // - smaller goes to left (farthest first so nearer sits next to center)
    // - larger goes to right (nearest first so nearer sits next to center)
    function orderByNextClusterDistance(keys, currentCluster) {
        const curr = String(currentCluster ?? "").trim();
        const currNum = parseClusterNumber(curr);

        const center = [];
        const left = [];
        const right = [];
        const other = [];

        for (const k of keys) {
            const kk = String(k ?? "").trim();
            if (!kk) continue;
            if (kk === curr) {
                center.push(kk);
                continue;
            }
            if (kk === "End" || kk === "Gone") {
                other.push(kk);
                continue;
            }
            const kn = parseClusterNumber(kk);
            if (currNum !== null && kn !== null) {
                if (kn < currNum) left.push({ key: kk, dist: Math.abs(currNum - kn), num: kn });
                else right.push({ key: kk, dist: Math.abs(currNum - kn), num: kn });
            } else {
                // Fallback: lexical side
                const cmp = compareClusterLabel(kk, curr);
                if (cmp < 0) left.push({ key: kk, dist: 1, num: null });
                else right.push({ key: kk, dist: 1, num: null });
            }
        }

        // Left: far -> near (so near ends up close to center)
        left.sort((a, b) => {
            if (a.dist !== b.dist) return b.dist - a.dist;
            return compareClusterLabel(a.key, b.key);
        });
        // Right: near -> far
        right.sort((a, b) => {
            if (a.dist !== b.dist) return a.dist - b.dist;
            return compareClusterLabel(a.key, b.key);
        });

        // Keep End/Gone at the far right by default, with stable ordering
        other.sort((a, b) => a.localeCompare(b));

        return [
            ...left.map((x) => x.key),
            ...center,
            ...right.map((x) => x.key),
            ...other,
        ];
    }
    
    // Calculate similarity between two trend sequences
    // Returns a similarity score (0-1, where 1 means identical)
    function calculateTrendSimilarity(seq1, seq2) {
        if (!seq1 || !seq2 || seq1.length !== seq2.length) {
            return 0;
        }
        
        let matchCount = 0;
        let validCount = 0;
        let numericSimilaritySum = 0;
        let numericCount = 0;
        
        for (let i = 0; i < seq1.length; i++) {
            const c1 = seq1[i];
            const c2 = seq2[i];
            
            if (c1 === null || c2 === null) {
                if (c1 === c2) {
                    matchCount++;
                }
                continue;
            }
            
            validCount++;
            
            // Exact match
            if (c1 === c2) {
                matchCount++;
                continue;
            }
            
            // Try numeric comparison for similarity
            const num1 = Number.parseFloat(c1);
            const num2 = Number.parseFloat(c2);
            
            if (!Number.isNaN(num1) && !Number.isNaN(num2)) {
                // Calculate numeric similarity based on relative difference
                // Get the range of all numeric values in both sequences
                const allNums = [];
                seq1.forEach(c => {
                    if (c !== null) {
                        const n = Number.parseFloat(c);
                        if (!Number.isNaN(n)) allNums.push(n);
                    }
                });
                seq2.forEach(c => {
                    if (c !== null) {
                        const n = Number.parseFloat(c);
                        if (!Number.isNaN(n)) allNums.push(n);
                    }
                });
                
                if (allNums.length > 0) {
                    const min = Math.min(...allNums);
                    const max = Math.max(...allNums);
                    const range = max - min;
                    
                    if (range > 0) {
                        const normalized1 = (num1 - min) / range;
                        const normalized2 = (num2 - min) / range;
                        const diff = Math.abs(normalized1 - normalized2);
                        const similarity = 1 - Math.min(diff, 1); // 1 when same, 0 when max diff
                        numericSimilaritySum += similarity;
                        numericCount++;
                    }
                }
            }
        }
        
        // Combine exact matches and numeric similarity
        const exactMatchScore = validCount > 0 ? matchCount / validCount : 0;
        const numericSimilarityScore = numericCount > 0 ? numericSimilaritySum / numericCount : 0;
        
        // Weighted combination: exact matches are more important
        return exactMatchScore * 0.7 + numericSimilarityScore * 0.3;
    }
    
    // Check if a trend sequence is stable (all elements are the same)
    function isTrendStable(sequence) {
        if (!sequence || sequence.length === 0) return false;
        const first = sequence[0];
        if (first === null) return false;
        return sequence.every(cluster => cluster === first);
    }
    
    // Sort trends by stability first (stable trends first), then by similarity
    function sortTrendsBySimilarity(trendGroups) {
        const trendEntries = Array.from(trendGroups.entries()).map(([key, group]) => ({
            key,
            sequence: group.sequence,
            group,
            isStable: isTrendStable(group.sequence),
        }));
        
        if (trendEntries.length <= 1) {
            return trendEntries.map(e => e.key);
        }
        
        // Separate stable and non-stable trends
        const stableTrends = trendEntries.filter(e => e.isStable);
        const nonStableTrends = trendEntries.filter(e => !e.isStable);
        
        // Sort stable trends by their cluster value (for consistent ordering)
        stableTrends.sort((a, b) => {
            const aVal = a.sequence[0] || '';
            const bVal = b.sequence[0] || '';
            const numA = Number.parseFloat(aVal);
            const numB = Number.parseFloat(bVal);
            if (!Number.isNaN(numA) && !Number.isNaN(numB)) {
                return numA - numB;
            }
            return String(aVal).localeCompare(String(bVal));
        });
        
        // Sort non-stable trends by similarity
        if (nonStableTrends.length <= 1) {
            // If only one or zero non-stable trends, just combine them
            const ordered = stableTrends.concat(nonStableTrends);
            return ordered.map(e => e.key);
        }
        
        // Build similarity matrix for non-stable trends: index -> index -> similarity
        const similarities = [];
        for (let i = 0; i < nonStableTrends.length; i++) {
            similarities[i] = [];
            for (let j = 0; j < nonStableTrends.length; j++) {
                if (i === j) {
                    similarities[i][j] = 1.0;
                } else if (j < i) {
                    similarities[i][j] = similarities[j][i];
                } else {
                    similarities[i][j] = calculateTrendSimilarity(
                        nonStableTrends[i].sequence,
                        nonStableTrends[j].sequence
                    );
                }
            }
        }
        
        // Greedy ordering for non-stable trends: start with first trend, then always pick the most similar to the last added
        const orderedNonStable = [0];
        const remaining = Array.from({ length: nonStableTrends.length - 1 }, (_, i) => i + 1);
        
        while (remaining.length > 0) {
            const lastIdx = orderedNonStable[orderedNonStable.length - 1];
            let bestNextIdx = remaining[0];
            let bestSimilarity = similarities[lastIdx][bestNextIdx];
            
            // Find the trend most similar to the last added trend
            for (const candidateIdx of remaining) {
                const sim = similarities[lastIdx][candidateIdx];
                if (sim > bestSimilarity) {
                    bestSimilarity = sim;
                    bestNextIdx = candidateIdx;
                }
            }
            
            orderedNonStable.push(bestNextIdx);
            const removeIdx = remaining.indexOf(bestNextIdx);
            remaining.splice(removeIdx, 1);
        }
        
        // Combine: stable trends first, then non-stable trends sorted by similarity
        const orderedNonStableEntries = orderedNonStable.map(idx => nonStableTrends[idx]);
        const finalOrdered = stableTrends.concat(orderedNonStableEntries);
        
        return finalOrdered.map(e => e.key);
    }
    
    // Check if a barcode is in the same cluster across all results
    function isBarcodeStable(barcode) {
        if (!barcodeClusterDistribution.has(barcode)) {
            return false;
        }
        const clusterMap = barcodeClusterDistribution.get(barcode);
        // If there's only one cluster entry, and its count equals the number of results,
        // then the barcode is in the same cluster across all results
        if (clusterMap.size === 1) {
            const totalResults = visibleDistributionSummary.length;
            const clusterEntry = Array.from(clusterMap.values())[0];
            // Check if the count matches the number of results
            return clusterEntry.count === totalResults;
        }
        return false;
    }

    function getBarcodeStabilityPercent(barcode) {
        const bc = barcode === null || barcode === undefined ? "" : `${barcode}`.trim();
        if (!bc) return 0;
        return barcodeStabilityPercent.get(bc) ?? 0;
    }

    function isBarcodeFilteredOutByStability(barcode) {
        return getBarcodeStabilityPercent(barcode) > maxStabilityPercent;
    }
    
    // NOTE: table collapse toggling is now handled inside `MetricsComparison` (clusterTables variant),
    // but we keep `isBelowCollapsed` (bound from LabelResultClusterSpotCircular) for spatial preview & pie chart size.
    
    // Cache for missing metrics warnings to avoid repeated console output
    const missingMetricsWarnings = new Set();
    
    // Color scale functions for metrics
    function getMetricColor(metricName, value, min = null, max = null) {
        if (value === null || value === undefined) {
            return 'rgba(226, 232, 240, 0.5)'; // slate-200 with opacity
        }
        
        // Normalize value to 0-1 range
        let normalized = 0;
        if (metricName === 'silhouette') {
            // Silhouette: -1 to 1, but typically 0 to 1, higher is better
            const vMin = min !== null ? min : -1;
            const vMax = max !== null ? max : 1;
            normalized = (value - vMin) / (vMax - vMin);
            normalized = Math.max(0, Math.min(1, normalized));
            // Gray scale: light gray (low) to dark gray (high)
            const intensity = Math.floor(normalized * 255);
            return `rgba(100, 116, 139, ${0.3 + normalized * 0.5})`; // slate-500
        } else if (metricName === 'morans_i') {
            // Moran's I: -1 to 1, higher is better (spatial clustering)
            const vMin = min !== null ? min : -1;
            const vMax = max !== null ? max : 1;
            normalized = (value - vMin) / (vMax - vMin);
            normalized = Math.max(0, Math.min(1, normalized));
            // Green scale: light green (low) to dark green (high)
            return `rgba(34, 197, 94, ${0.3 + normalized * 0.5})`; // green-500
        } else if (metricName === 'gearys_c') {
            // Geary's C: typically 0 to 2, lower is better (<1 = positive autocorrelation)
            // Invert: low value (good) = high color intensity
            const vMin = min !== null ? min : 0;
            const vMax = max !== null ? max : 2;
            normalized = (value - vMin) / (vMax - vMin);
            normalized = Math.max(0, Math.min(1, normalized));
            // Inverted: low values (good) = high intensity, high values (bad) = low intensity
            normalized = 1 - normalized;
            // Purple scale: light purple (bad/high C) to dark purple (good/low C)
            return `rgba(168, 85, 247, ${0.3 + normalized * 0.5})`; // purple-500
        }
        return 'rgba(226, 232, 240, 0.5)';
    }
    
    // Calculate min/max for each metric across all results for normalization
    function getMetricRange(metricName) {
        let min = null;
        let max = null;
        
        clusterMetricsData.forEach((clusterMap) => {
            clusterMap.forEach((metrics) => {
                const value = metrics[metricName];
                if (value !== null && value !== undefined) {
                    if (min === null || value < min) min = value;
                    if (max === null || value > max) max = value;
                }
            });
        });
        
        // Set default ranges if no data
        if (min === null || max === null) {
            if (metricName === 'silhouette') return { min: -1, max: 1 };
            if (metricName === 'morans_i') return { min: -1, max: 1 };
            if (metricName === 'gearys_c') return { min: 0, max: 2 };
        }
        
        // Add some padding
        const padding = (max - min) * 0.1 || 0.1;
        return { min: min - padding, max: max + padding };
    }

    const getResultLabel = (summary) => {
        const { result } = summary ?? {};
        return (
            result?.result_name ||
            result?.cluster_result_id ||
            result?.result_id ||
            "Unnamed result"
        );
    };

    function sanitizeBarcode(value) {
        if (value === null || value === undefined) return null;
        if (Array.isArray(value)) {
            return value.length ? sanitizeBarcode(value[0]) : null;
        }
        const normalized = `${value}`.trim();
        return normalized || null;
    }

    /**
     * Convert color to rgba format with specified alpha
     * Supports rgb(), rgba(), hex, and named colors
     */
    function colorWithAlpha(color, alpha = 0.35) {
        if (!color) return `rgba(148, 163, 184, ${alpha})`;
        
        const colorStr = String(color).trim();
        
        // Already rgba with alpha
        const rgbaMatch = colorStr.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*[\d.]+)?\)/);
        if (rgbaMatch) {
            return `rgba(${rgbaMatch[1]}, ${rgbaMatch[2]}, ${rgbaMatch[3]}, ${alpha})`;
        }
        
        // rgb() without alpha
        const rgbMatch = colorStr.match(/rgb\((\d+),\s*(\d+),\s*(\d+)\)/);
        if (rgbMatch) {
            return `rgba(${rgbMatch[1]}, ${rgbMatch[2]}, ${rgbMatch[3]}, ${alpha})`;
        }
        
        // Hex color (#RGB or #RRGGBB)
        const hexMatch = colorStr.match(/^#([0-9A-Fa-f]{3}|[0-9A-Fa-f]{6})$/);
        if (hexMatch) {
            let hex = hexMatch[1];
            if (hex.length === 3) {
                hex = hex.split('').map(c => c + c).join('');
            }
            const r = parseInt(hex.substring(0, 2), 16);
            const g = parseInt(hex.substring(2, 4), 16);
            const b = parseInt(hex.substring(4, 6), 16);
            return `rgba(${r}, ${g}, ${b}, ${alpha})`;
        }
        
        // Fallback: try to parse as CSS color name or return default
        try {
            const ctx = document.createElement('canvas').getContext('2d');
            ctx.fillStyle = colorStr;
            const computed = ctx.fillStyle;
            const rgbMatch2 = computed.match(/rgb\((\d+),\s*(\d+),\s*(\d+)\)/);
            if (rgbMatch2) {
                return `rgba(${rgbMatch2[1]}, ${rgbMatch2[2]}, ${rgbMatch2[3]}, ${alpha})`;
            }
        } catch (e) {
            // Ignore parsing errors
        }
        
        return `rgba(148, 163, 184, ${alpha})`;
    }

    async function ensureColorMapping(result, baseColor) {
        if (!result?.cluster_result_id) return new Map();

        const url = `${baseApi}/cluster-color-mapping?slice_id=${encodeURIComponent(
            currentSlice,
        )}&cluster_result_id=${encodeURIComponent(result.cluster_result_id)}`;
        try {
            const res = await fetch(url);
            if (!res.ok) throw new Error("color mapping fetch failed");
            const data = await res.json();
            const mapping = new Map();
            Object.entries(data?.color_mapping ?? {}).forEach(
                ([key, value]) => {
                    mapping.set(`${key}`.trim(), value || baseColor);
                },
            );
            return mapping;
        } catch (err) {
            console.warn(
                "[ClusterResultComparison] failed to fetch color mapping, fallback to base colors:",
                err,
            );
            return new Map();
        }
    }

    async function buildComparison(key, resultsToLoad = null) {
        const token = ++requestToken;
        console.log(`[ClusterResultComparison] buildComparison called with key: ${key}`);
        loading = true;
        error = null;
        distributionSummary = [];
        legendEntries = [];
        colorCache.clear();
        // 通知父组件：比较视图开始加载（Sankey / 折线 / 雷达等）
        dispatch("loadingChange", { loading: true });

        // Use provided results or fall back to clusterResults
        const results = resultsToLoad || clusterResults;
        
        if (!results?.length || !currentSlice) {
            loading = false;
            dispatch("loadingChange", { loading: false });
            return;
        }

        try {
            // ⚡ 并行加载每个结果的 plot-data 和颜色映射
            const summaryResults = await Promise.all(
                results.map(async (result, idx) => {
                const url = `${baseApi}/plot-data?slice_id=${encodeURIComponent(
                    currentSlice,
                )}&cluster_result_id=${encodeURIComponent(result.cluster_result_id)}`;
                const res = await fetch(url);
                if (!res.ok) {
                    throw new Error(
                        `Failed to load cluster assignments (${res.status}).`,
                    );
                }
                const traces = await res.json();
                    if (requestToken !== token) return null;

                const clusterBarcodes = new Map();

                (traces ?? []).forEach((trace) => {
                    const clusterName = `${trace?.name ?? "unknown"}`;
                    const rawBarcodes =
                        trace?.customdata ??
                        trace?.text ??
                        trace?.hovertext ??
                        [];
                    const barcodes = Array.isArray(rawBarcodes)
                        ? rawBarcodes
                        : typeof rawBarcodes === "string"
                          ? [rawBarcodes]
                          : rawBarcodes &&
                                  typeof rawBarcodes[Symbol.iterator] ===
                                      "function"
                            ? Array.from(rawBarcodes)
                            : [];

                    barcodes.forEach((barcode) => {
                        const sanitized = sanitizeBarcode(barcode);
                        if (!sanitized) return;
                        if (!clusterBarcodes.has(clusterName)) {
                            clusterBarcodes.set(clusterName, []);
                        }
                        clusterBarcodes.get(clusterName).push(sanitized);
                    });
                });

                    const clusterEntries = Array.from(
                        clusterBarcodes.entries(),
                    )
                    .filter(([, barcodes]) => barcodes.length > 0)
                    .sort((a, b) => {
                        const numA = Number.parseFloat(a[0]);
                        const numB = Number.parseFloat(b[0]);
                        const bothNumeric =
                            !Number.isNaN(numA) && !Number.isNaN(numB);
                        return bothNumeric
                            ? numA - numB
                            : String(a[0]).localeCompare(
                                  String(b[0]),
                                  undefined,
                                  {
                                      numeric: true,
                                  },
                              );
                    });

                const colorMapKey = `${currentSlice}:${result.cluster_result_id}`;
                let colorMapping = colorCache.get(colorMapKey);
                const baseColor = "rgba(148, 163, 184, 0.25)";
                if (!colorMapping) {
                        colorMapping = await ensureColorMapping(
                            result,
                            baseColor,
                        );
                    colorCache.set(colorMapKey, colorMapping);
                }

                const total = clusterEntries.reduce(
                    (acc, [, barcodes]) => acc + barcodes.length,
                    0,
                );
                let runningIndex = 0;
                const spots = [];

                    return {
                    result,
                    resultIndex: idx,
                    total,
                    spots,
                    clusters: clusterEntries.map(([cluster, barcodes]) => {
                        const count = barcodes.length;
                            const percent =
                                total > 0 ? (count / total) * 100 : 0;
                        const startIndex = runningIndex;
                        runningIndex += count;
                        const clusterColor =
                            colorMapping.get(`${cluster}`.trim()) ??
                            colorMapping.get(`${cluster}`) ??
                            baseColor;
                        const sanitizedBarcodes = barcodes.map((value) =>
                            `${value}`.trim(),
                        );
                        sanitizedBarcodes.forEach((barcode, offset) => {
                            spots.push({
                                barcode,
                                index: startIndex + offset,
                                color: clusterColor,
                                cluster,
                            });
                        });
                        return {
                            cluster,
                            count,
                            percent,
                            color: clusterColor,
                            startIndex,
                            barcodes: sanitizedBarcodes,
                        };
                    }),
                    };
                }),
            );

            if (requestToken !== token) return;

            // layoutCache moved to sankeyDiagram.svelte component
            // 过滤掉因为 token 变化返回的 null
            distributionSummary = summaryResults.filter(Boolean);
            
            // Compute per-barcode trend metadata (but DO NOT reorder spots here).
            // Ordering is handled purely at render time (measureConnections),
            // so the first row keeps the backend/default order as requested.
            if (distributionSummary.length > 0) {
                const barcodeTrends = new Map();
                const allBarcodes = new Set();
            distributionSummary.forEach((summary) => {
                    summary.spots?.forEach((spot) => {
                        allBarcodes.add(`${spot.barcode}`.trim());
                    });
                });
                allBarcodes.forEach((barcode) => {
                    const trend = calculateBarcodeTrend(barcode, distributionSummary);
                    barcodeTrends.set(barcode, trend);
                });
                distributionSummary.forEach((summary) => {
                    summary.spots?.forEach((spot) => {
                        const barcode = `${spot.barcode}`.trim();
                        const trend = barcodeTrends.get(barcode);
                        if (trend) {
                            spot.trendKey = trend.key;
                            spot.trendSequence = trend.sequence;
                            const clusterSet = buildClusterSetFromTrendSequence(trend.sequence);
                            spot.clusterSet = clusterSet;
                            spot.clusterSetKey = clusterSetKeyFromSet(clusterSet);
                        }
                    });
                });
            }
            
            // Build barcode cluster distribution map for pie chart
            buildBarcodeClusterDistribution();
            
            // Load metrics for each result
            await loadMetricsForResults();
            // Load cluster-level metrics for grid view
            await loadClusterMetricsForResults();
            
            // 确保spatial preview在buildComparison完成后重绘
            if (spatialPreviewDiv && spatialData && spatialData.length && image) {
                await tick();
                setTimeout(() => {
                    drawSpatialPreviewPlot();
                }, 100);
            }
        } catch (err) {
            if (requestToken !== token) return;
            console.error("[ClusterResultComparison] comparison error:", err);
            error =
                err?.message ??
                "Unable to compare cluster results. Please try again later.";
            distributionSummary = [];
        } finally {
            if (requestToken === token) {
                loading = false;
                // 通知父组件：比较视图加载结束
                dispatch("loadingChange", { loading: false });
            }
        }
    }

    async function loadMetricsForResults() {
        if (!clusterResults?.length || !currentSlice) {
            metricsData.clear();
            return;
        }
        
        // ⚡ 并行加载每个结果的 metrics
        await Promise.all(
            clusterResults.map(async (result) => {
            const resultId = result.cluster_result_id;
            // Use existing metrics if available
                if (
                    result.metrics &&
                    Object.values(result.metrics).some(
                        (v) => v !== null && v !== undefined,
                    )
                ) {
                metricsData.set(resultId, result.metrics);
                    return;
            }
            
            // Otherwise, compute metrics
            try {
                const res = await fetch(
                        `${baseApi}/compute-clustering-metrics?slice_id=${encodeURIComponent(
                            currentSlice,
                        )}&cluster_result_id=${encodeURIComponent(resultId)}`,
                        { method: "POST" },
                );
                if (res.ok) {
                    const data = await res.json();
                    metricsData.set(resultId, data.metrics || {});
                }
            } catch (err) {
                    console.error(
                        `[ClusterResultComparison] Failed to load metrics for ${resultId}:`,
                        err,
                    );
            }
            }),
        );
    }
    
    async function loadClusterMetricsForResults() {
        if (!clusterResults?.length || !currentSlice) {
            clusterMetricsData.clear();
            return;
        }
        
        // ⚡ 并行加载每个结果的 cluster-level metrics
        await Promise.all(
            clusterResults.map(async (result) => {
            const resultId = result.cluster_result_id;
            try {
                const response = await fetch(
                        `${baseApi}/cluster-metrics?slice_id=${encodeURIComponent(
                            currentSlice,
                        )}&cluster_result_id=${encodeURIComponent(resultId)}`,
                );
                if (response.ok) {
                    const data = await response.json();
                    if (data.cluster_metrics) {
                        // Convert array to map for easy lookup
                        // Normalize cluster names to match distributionSummary format
                        const clusterMap = new Map();
                            data.cluster_metrics.forEach((metrics) => {
                            // Normalize cluster name: convert to string and handle both "2.0" and "2" formats
                                const clusterName = String(
                                    metrics.cluster,
                                ).trim();
                            clusterMap.set(clusterName, metrics);
                            // Also add normalized version (e.g., "2.0" -> "2" and "2" -> "2.0")
                            const numValue = parseFloat(clusterName);
                            if (!isNaN(numValue)) {
                                // Add both formats for compatibility
                                const normalized1 = String(numValue);
                                    const normalized2 =
                                        numValue % 1 === 0
                                            ? String(numValue)
                                            : numValue.toFixed(1);
                                if (normalized1 !== clusterName) {
                                    clusterMap.set(normalized1, metrics);
                                }
                                    if (
                                        normalized2 !== clusterName &&
                                        normalized2 !== normalized1
                                    ) {
                                    clusterMap.set(normalized2, metrics);
                                }
                            }
                        });
                        clusterMetricsData.set(resultId, clusterMap);
                            console.log(
                                `[ClusterResultComparison] Loaded cluster metrics for ${resultId}:`,
                                Array.from(clusterMap.keys()),
                            );
                    }
                } else {
                        console.warn(
                            `[ClusterResultComparison] Failed to load cluster metrics for ${resultId}: ${response.status} ${response.statusText}`,
                        );
                }
            } catch (err) {
                    console.error(
                        `[ClusterResultComparison] Failed to load cluster metrics for ${resultId}:`,
                        err,
                    );
            }
            }),
        );
    }
    
    function drawMetricsCharts() {
        // layoutCache moved to sankeyDiagram.svelte component
        // Metrics charts may need to be updated to work without layoutCache
        if (!visibleDistributionSummary?.length) {
            return;
        }
        
        const METRICS_WIDTH = 100; // Width of each metric chart (increased to accommodate labels)
        const dpr = typeof window !== "undefined" ? window.devicePixelRatio || 1 : 1;
        
        // Get the content height from layout (same as Sankey)
        const CARD_HEIGHT = 80;
        const CARD_GAP = 10;
        const LEADING_MARGIN = 12;
        const TRAILING_MARGIN = 12;
        const rowCount = visibleDistributionSummary.length;
        const contentHeight = rowCount > 0
            ? LEADING_MARGIN + rowCount * CARD_HEIGHT + Math.max(0, rowCount - 1) * CARD_GAP + TRAILING_MARGIN
            : 400;
        
        // Draw each metric
        const metrics = [
            { key: 'chao', canvas: metricsCanvasChao, label: 'CH', higherIsBetter: true },
            { key: 'silhouette', canvas: metricsCanvasSilhouette, label: 'Silhouette', higherIsBetter: true },
            { key: 'pas', canvas: metricsCanvasPas, label: 'PAS', higherIsBetter: false },
            { key: 'morans_i', canvas: metricsCanvasMorans, label: "Moran's I", higherIsBetter: true },
        ];
        
        // Clear points cache
        metricsPointsCache.clear();
        
        // Collect all values to determine range
        const metricRanges = {};
        metrics.forEach(({ key }) => {
            const values = visibleDistributionSummary
                .map(s => {
                    const resultId = s.result?.cluster_result_id;
                    const m = metricsData.get(resultId);
                    return m?.[key];
                })
                .filter(v => v !== null && v !== undefined);
            if (values.length > 0) {
                metricRanges[key] = {
                    min: Math.min(...values),
                    max: Math.max(...values),
                };
            } else {
                metricRanges[key] = { min: 0, max: 1 };
            }
        });
        
        metrics.forEach(({ key, canvas, label, higherIsBetter }) => {
            if (!canvas) return;
            
            const canvasWidth = Math.floor(METRICS_WIDTH * dpr);
            const canvasHeight = Math.floor(contentHeight * dpr);
            
            canvas.width = canvasWidth;
            canvas.height = canvasHeight;
            canvas.style.width = `${METRICS_WIDTH}px`;
            canvas.style.height = `${contentHeight}px`;
            
            const ctx = canvas.getContext("2d");
            if (!ctx) return;
            
            ctx.setTransform(1, 0, 0, 1, 0, 0);
            ctx.clearRect(0, 0, canvasWidth, canvasHeight);
            ctx.scale(dpr, dpr);
            
            const range = metricRanges[key];
            const padding = { left: 4, right: 4, top: LEADING_MARGIN, bottom: TRAILING_MARGIN };
            const chartWidth = METRICS_WIDTH - padding.left - padding.right;
            
            // Collect points for all results
            const points = [];
            visibleDistributionSummary.forEach((summary, idx) => {
                const resultId = summary.result?.cluster_result_id;
                const value = metricsData.get(resultId)?.[key];
                
                if (value === null || value === undefined) return;
                
                const cardTop = LEADING_MARGIN + idx * (CARD_HEIGHT + CARD_GAP);
                const rowCenter = cardTop + CARD_HEIGHT / 2;
                
                // Normalize value to [0, 1] within range
                const normalizedValue = range.max > range.min
                    ? (value - range.min) / (range.max - range.min)
                    : 0.5;
                
                // X position in chart (higher is better: right side, lower is better: left side)
                const xPos = padding.left + (higherIsBetter ? normalizedValue : (1 - normalizedValue)) * chartWidth;
                
                points.push({ x: xPos, y: rowCenter, value, summary, resultId: resultId });
            });
            
            // Cache points for hit testing
            metricsPointsCache.set(key, points);
            
            // Draw vertical connecting lines between points (折线图)
            ctx.strokeStyle = "#64748b"; // Gray color
            ctx.lineWidth = 2;
            ctx.lineCap = "round";
            ctx.lineJoin = "round";
            
            if (points.length > 1) {
                ctx.beginPath();
                ctx.moveTo(points[0].x, points[0].y);
                for (let i = 1; i < points.length; i++) {
                    ctx.lineTo(points[i].x, points[i].y);
                }
                ctx.stroke();
            }
            
            // Draw points and labels
            points.forEach(({ x, y, value }, idx) => {
                // Draw point
                ctx.fillStyle = "#64748b"; // Gray color
                ctx.beginPath();
                ctx.arc(x, y, 3, 0, Math.PI * 2);
                ctx.fill();
                
                // Draw value label with smart placement to avoid clipping
                ctx.fillStyle = "#64748b";
                ctx.font = "10px/12px system-ui, sans-serif";
                ctx.textBaseline = "middle";
                
                const labelText = value.toFixed(2);
                const textMetrics = ctx.measureText(labelText);
                const textWidth = textMetrics.width;
                const textHeight = 10;
                
                // Prefer placing to the right; if it overflows, place to the left
                const rightLimit = METRICS_WIDTH - padding.right;
                const leftLimit = padding.left;
                const preferRight = x + 5 + textWidth + 4 <= rightLimit;
                const labelX = preferRight ? x + 5 : x - 5;
                ctx.textAlign = preferRight ? "left" : "right";
                
                // Background rect
                ctx.fillStyle = "rgba(255, 255, 255, 0.9)";
                const bgX = preferRight ? (labelX - 2) : (labelX - (textWidth + 2));
                const bgY = y - textHeight / 2 - 1;
                ctx.fillRect(bgX, bgY, textWidth + 4, textHeight + 2);
                
                // Label text
                ctx.fillStyle = "#64748b";
                ctx.fillText(labelText, labelX, y);
            });
            
            // Draw axis
            ctx.strokeStyle = "#e2e8f0";
            ctx.lineWidth = 1;
            ctx.beginPath();
            ctx.moveTo(padding.left, padding.top);
            ctx.lineTo(padding.left, contentHeight - padding.bottom);
            ctx.stroke();
            
            // Draw label
            ctx.fillStyle = "#475569";
            ctx.font = "11px/14px system-ui, sans-serif";
            ctx.textAlign = "center";
            ctx.save();
            ctx.translate(METRICS_WIDTH / 2, contentHeight / 2);
            ctx.rotate(-Math.PI / 2);
            ctx.fillText(label, 0, 0);
            ctx.restore();
        });
    }
    
    
    function resetComparison() {
        error = null;
        distributionSummary = [];
        legendEntries = [];
        colorCache.clear();
        metricsData.clear();
        // layoutCache and clearCanvas moved to sankeyDiagram.svelte component
    }

    $: {
        // comparisonKey should depend on ALL result IDs (including hidden ones) to avoid reloading when hiding/showing
        // Use allClusterResults if provided, otherwise fall back to clusterResults
        const resultsForKey = allClusterResults && allClusterResults.length > 0
            ? allClusterResults
            : clusterResults;

        const key =
            resultsForKey?.length && currentSlice
                ? `${currentSlice}::${[...new Set(resultsForKey.map((item) => item?.cluster_result_id))]
                      .filter(Boolean)
                      .sort()
                      .join("|")}`
                : "";

        if (key !== comparisonKey) {
            console.log(`[ClusterResultComparison] comparisonKey changed from "${comparisonKey}" to "${key}" (isLayoutChangeInProgress: ${isLayoutChangeInProgress})`);
            comparisonKey = key;
            if (comparisonKey) {
                // 如果正在进行布局变化，不要触发数据重新加载
                if (!isLayoutChangeInProgress) {
                    // Use allClusterResults for loading to load all data including hidden ones
                    buildComparison(comparisonKey, resultsForKey);
                } else {
                    console.log(`[ClusterResultComparison] Skipping buildComparison during layout change`);
                }
            } else {
                resetComparison();
            }
        }
    }
    
    // When clusterResults order changes (due to sorting), just reorder distributionSummary
    // without reloading data
    $: if (distributionSummary.length > 0 && clusterResults.length > 0) {
        // Create a map of result_id -> summary for quick lookup
        const summaryMap = new Map();
        distributionSummary.forEach(summary => {
            const resultId = summary.result?.cluster_result_id;
            if (resultId) {
                summaryMap.set(resultId, summary);
            }
        });
        
        // Reorder distributionSummary to match clusterResults order
        const reorderedSummary = [];
        clusterResults.forEach((result, idx) => {
            const resultId = result.cluster_result_id;
            const summary = summaryMap.get(resultId);
            if (summary) {
                // Update resultIndex to match new position
                summary.resultIndex = idx;
                reorderedSummary.push(summary);
            }
        });
        
        // Only update if order actually changed
        if (reorderedSummary.length === distributionSummary.length) {
            let orderChanged = false;
            for (let i = 0; i < reorderedSummary.length; i++) {
                if (reorderedSummary[i].result?.cluster_result_id !== distributionSummary[i].result?.cluster_result_id) {
                    orderChanged = true;
                    break;
                }
            }
            if (orderChanged) {
                distributionSummary = reorderedSummary;
                // Trigger canvas redraw - handled by sankeyDiagram.svelte component
            }
        }
    }

    function buildBarcodeClusterDistribution() {
        barcodeClusterDistribution.clear();
        barcodeStabilityPercent.clear();
        
        visibleDistributionSummary.forEach((summary) => {
            if (!summary.spots || !summary.clusters) return;
            
            // Build cluster color map
            const clusterColorMap = new Map();
            summary.clusters.forEach((cluster) => {
                clusterColorMap.set(String(cluster.cluster).trim(), cluster.color);
            });
            
            // Count barcode occurrences in each cluster
            summary.spots.forEach((spot) => {
                const barcode = `${spot.barcode}`.trim();
                const cluster = String(spot.cluster).trim();
                const color = clusterColorMap.get(cluster) || spot.color;
                
                if (!barcodeClusterDistribution.has(barcode)) {
                    barcodeClusterDistribution.set(barcode, new Map());
                }
                
                const clusterMap = barcodeClusterDistribution.get(barcode);
                if (!clusterMap.has(cluster)) {
                    clusterMap.set(cluster, { count: 0, color });
                }
                clusterMap.get(cluster).count += 1;
            });
        });

        // Compute stability percent for each barcode
        const totalResults = visibleDistributionSummary.length || 0;
        barcodeClusterDistribution.forEach((clusterMap, barcode) => {
            if (!totalResults) {
                barcodeStabilityPercent.set(barcode, 0);
                return;
            }
            let maxCount = 0;
            clusterMap.forEach((entry) => {
                if (entry?.count > maxCount) maxCount = entry.count;
            });
            barcodeStabilityPercent.set(barcode, (maxCount / totalResults) * 100);
        });
    }

    $: if (visibleDistributionSummary?.length) {
        const colorMap = visibleDistributionSummary.reduce((map, summary) => {
            summary.clusters.forEach(({ cluster, color }) => {
                const key = `${cluster}`.trim();
                if (!map.has(key)) {
                    map.set(key, color);
                }
            });
            return map;
        }, new Map());

        legendEntries = Array.from(colorMap, ([cluster, color]) => ({
            cluster,
            color,
        }));
    } else {
        legendEntries = [];
    }

    // scheduleMeasure and measureConnections moved to sankeyDiagram.svelte component
    function toBase64(img) {
        const canvas = document.createElement("canvas");
        canvas.width = img.width;
        canvas.height = img.height;
        const ctx = canvas.getContext("2d");
        ctx.drawImage(img, 0, 0);
        return canvas.toDataURL("image/png");
    }

    /** 防抖版本的 drawSpatialPreviewPlot，避免短时间内多次调用导致性能问题 */
    function debouncedDrawSpatialPreviewPlot(delay = 100) {
        if (drawSpatialPreviewTimeout) {
            clearTimeout(drawSpatialPreviewTimeout);
        }
        drawSpatialPreviewTimeout = setTimeout(() => {
            drawSpatialPreviewTimeout = null;
            drawSpatialPreviewPlot();
        }, delay);
    }

    /** 防抖版本的 drawAllPieCharts */
    function debouncedDrawAllPieCharts(highlightBarcode = null, delay = 100) {
        if (drawAllPieChartsTimeout) {
            clearTimeout(drawAllPieChartsTimeout);
        }
        drawAllPieChartsTimeout = setTimeout(() => {
            drawAllPieChartsTimeout = null;
            drawAllPieCharts(highlightBarcode);
        }, delay);
    }

    async function drawSpatialPreviewPlot() {
        // Remove old event listeners before purging
        if (spatialPreviewPlotInstance) {
            try {
                spatialPreviewPlotInstance.removeAllListeners?.("plotly_hover");
                spatialPreviewPlotInstance.removeAllListeners?.("plotly_unhover");
            } catch (e) {
                // Ignore errors if removeAllListeners doesn't exist
            }
            spatialPreviewEventsBound = false;
        }
        
        if (spatialPreviewPlotInstance && spatialPreviewDiv) {
            Plotly.purge(spatialPreviewDiv);
            spatialPreviewPlotInstance = null;
        }

        if (!image || !spatialData || !spatialData.length) {
            return;
        }

        if (!spatialPreviewDiv || !spatialPreviewDiv.isConnected) {
            return;
        }

        const base64 = toBase64(image);
        const layout = {
            autosize: true,
            xaxis: {
                visible: false,
                range: [0, image.width],
            },
            yaxis: {
                visible: false,
                range: [image.height, 0], // y轴反向，与Plot组件一致
                scaleanchor: "x",
                scaleratio: 1,
            },
            dragmode: false,
            margin: { l: 0, r: 0, t: 0, b: 0 },
            showlegend: false,
            plot_bgcolor: "transparent",
            paper_bgcolor: "transparent",
            hovermode: "closest",
            hoverlabel: {
                bgcolor: "rgba(0, 0, 0, 0.9)",
                bordercolor: "rgba(255, 255, 255, 0.3)",
                font: { size: 12, color: "white" }
            },
            images: [
                {
                    source: base64,
                    xref: "x",
                    yref: "y",
                    x: 0,
                    y: 0,
                    sizex: image.width,
                    sizey: image.height,
                    sizing: "contain",
                    xanchor: "left",
                    yanchor: "top",
                    opacity: 0.6,
                    layer: "below",
                },
            ],
        };

        // 将所有点合并成一个基础灰色 trace + 一个高亮 trace（用于按 barcode 高亮）
        const allX = [];
        const allY = [];
        const allBarcodes = [];
        spatialPreviewBarcodePoints = new Map();

        spatialData.forEach((trace) => {
            const xs = Array.isArray(trace?.x) ? trace.x : [];
            const ys = Array.isArray(trace?.y) ? trace.y : [];
            const barcodes =
                Array.isArray(trace?.customdata) && trace.customdata.length === xs.length
                    ? trace.customdata
                    : Array.isArray(trace?.text) && trace.text.length === xs.length
                    ? trace.text
                    : new Array(xs.length).fill(null);

            const n = Math.min(xs.length, ys.length, barcodes.length);
            for (let i = 0; i < n; i += 1) {
                const px = Number(xs[i]);
                const py = Number(ys[i]);
                if (Number.isFinite(px) && Number.isFinite(py)) {
                    const bcRaw = barcodes[i];
                    const bc =
                        bcRaw === null || bcRaw === undefined
                            ? null
                            : `${bcRaw}`.trim() || null;

                    // Filter out barcodes above stability threshold
                    if (bc && barcodeStabilityPercent && barcodeStabilityPercent.size > 0 && isBarcodeFilteredOutByStability(bc)) {
                        continue;
                    }

                    allX.push(px);
                    allY.push(py);
                    allBarcodes.push(bc);

                    if (bc) {
                        let entry = spatialPreviewBarcodePoints.get(bc);
                        if (!entry) {
                            entry = { xs: [], ys: [] };
                            spatialPreviewBarcodePoints.set(bc, entry);
                        }
                        entry.xs.push(px);
                        entry.ys.push(py);
                    }
                }
            }
        });

        const baseTrace = {
            x: allX,
            y: allY,
            customdata: allBarcodes, // Store barcode for each point
            type: "scatter",
            mode: "markers",
            marker: {
                color: "rgba(100, 116, 139, 0.55)", // slate-500 with opacity
                size: 4, // Slightly larger for easier hovering
            },
            hoverinfo: "all", // Enable all hover information
            text: allBarcodes.map(bc => bc || ""),
            hovertemplate: "Barcode: %{text}<extra></extra>",
        };

        const highlightTrace = {
            x: [],
            y: [],
            type: "scatter",
            mode: "markers",
            marker: {
                color: "rgba(234, 88, 12, 0.9)", // amber-600
                size: 6,
            },
            hoverinfo: "skip",
        };

        const traces = [baseTrace, highlightTrace];

        try {
            spatialPreviewPlotInstance = await Plotly.newPlot(
                spatialPreviewDiv,
                traces,
                layout,
                {
                    displayModeBar: false,
                    staticPlot: false, // Enable hover events
                },
            );

            // 每次 Plotly 重绘后：把饼图 canvas 移入 plot 容器并提高 hoverlayer z-index，让 tooltip 显示在饼图之上
            spatialPreviewPlotInstance.on('plotly_afterplot', ensureTooltipAbovePieCanvas);

            // Bind events to the Plotly instance (same as Plot component)
            // Only bind once to avoid duplicate listeners
            if (spatialPreviewPlotInstance && !spatialPreviewEventsBound) {
                spatialPreviewPlotInstance.on("plotly_hover", (data) => {
                    if (data.points && data.points.length > 0) {
                        const point = data.points[0];
                        // Try to get barcode from customdata first, then text
                        const barcode = point.customdata !== undefined && point.customdata !== null 
                            ? point.customdata 
                            : (point.text || null);
                        if (barcode) {
                            const bcStr = `${barcode}`.trim();
                            if (bcStr) {
                                highlightedBarcodeFromPreview = bcStr;
                                
                                // Show pie chart at point location
                                if (!showAllPieCharts) {
                                    // 非展示所有饼图时，只显示饼图，不显示橙色高亮点
                                    const entry = spatialPreviewBarcodePoints.get(bcStr);
                                    if (entry && entry.xs.length > 0 && entry.ys.length > 0) {
                                        const plotlyX = entry.xs[0];
                                        const plotlyY = entry.ys[0];
                                        const pixelCoords = plotlyToPixelCoords(plotlyX, plotlyY);
                                        if (pixelCoords) {
                                            drawPieChart(bcStr, pixelCoords.x, pixelCoords.y);
                                        }
                                    }
                                } else {
                                    // 展示所有饼图时，只通过饼图透明度高亮
                                    allPieChartsHighlightBarcode = bcStr;
                                    drawAllPieCharts(bcStr);
                                }
                            }
                        }
                    }
                });

                spatialPreviewPlotInstance.on("plotly_unhover", () => {
                    highlightedBarcodeFromPreview = null;
                    // Clear pie chart or restore all pie charts
                    if (!showAllPieCharts) {
                        drawPieChart(null, 0, 0); // Clear pie chart
                    } else {
                        // 恢复所有饼图的正常显示（无高亮）
                        allPieChartsHighlightBarcode = null;
                        drawAllPieCharts(null);
                    }
                });
                
                spatialPreviewEventsBound = true;
            }

            ensureTooltipAbovePieCanvas();
            setTimeout(ensureTooltipAbovePieCanvas, 50);
            setTimeout(ensureTooltipAbovePieCanvas, 200);

            // Draw all pie charts if switch is enabled
            if (showAllPieCharts) {
                setTimeout(() => {
                    drawAllPieCharts();
                }, 200);
            }
        } catch (err) {
            console.error(
                "[ClusterResultComparison] Failed to draw spatial preview:",
                err,
            );
        }
    }

    function highlightSpatialPreviewByBarcode(barcode) {
        if (!spatialPreviewDiv || !spatialPreviewPlotInstance) return;

        const barcodeStr = barcode ? `${barcode}`.trim() : null;
        
        // Skip if already highlighting the same barcode
        if (currentHighlightedBarcode === barcodeStr) {
            return;
        }
        
        currentHighlightedBarcode = barcodeStr;

        // 清除高亮
        if (!barcodeStr) {
            Plotly.restyle(
                spatialPreviewDiv,
                {
                    x: [[]],
                    y: [[]],
                },
                [1],
            );
            return;
        }

        const entry = spatialPreviewBarcodePoints.get(barcodeStr);
        if (!entry || !entry.xs || !Array.isArray(entry.xs) || entry.xs.length === 0 || 
            !entry.ys || !Array.isArray(entry.ys) || entry.ys.length === 0) {
            // 没有对应点，也清空高亮
            Plotly.restyle(
                spatialPreviewDiv,
                {
                    x: [[]],
                    y: [[]],
                },
                [1],
            );
            return;
        }

        Plotly.restyle(
            spatialPreviewDiv,
            {
                x: [entry.xs],
                y: [entry.ys],
            },
            [1],
        );
    }

    function highlightSpatialPreviewByBarcodes(barcodes, color = null) {
        if (!spatialPreviewDiv || !spatialPreviewPlotInstance) return;

        // 清除高亮
        if (!barcodes || barcodes.length === 0) {
            // Only clear if not already cleared
            if (currentHighlightedBarcode && currentHighlightedBarcode.startsWith('__multiple__')) {
                Plotly.restyle(
                    spatialPreviewDiv,
                    {
                        x: [[]],
                        y: [[]],
                    },
                    [1],
                );
                currentHighlightedBarcode = null;
                currentHighlightColor = null;
            }
            return;
        }

        // 收集所有 barcode 对应的点并排序，用于比较
        const sortedBarcodes = barcodes.map(bc => `${bc}`.trim()).sort().join(',');
        const highlightKey = `__multiple__${sortedBarcodes}`;
        const colorStr = color || "rgba(234, 88, 12, 0.9)"; // Default orange if no color provided
        
        // Skip if already highlighting the same barcodes with the same color
        if (currentHighlightedBarcode === highlightKey && currentHighlightColor === colorStr) {
            return;
        }
        
        currentHighlightedBarcode = highlightKey;
        currentHighlightColor = colorStr;

        // 收集所有 barcode 对应的点
        const allX = [];
        const allY = [];
        barcodes.forEach((barcode) => {
            const barcodeStr = `${barcode}`.trim();
            const entry = spatialPreviewBarcodePoints.get(barcodeStr);
            if (entry && entry.xs && Array.isArray(entry.xs) && entry.xs.length > 0 &&
                entry.ys && Array.isArray(entry.ys) && entry.ys.length > 0) {
                allX.push(...entry.xs);
                allY.push(...entry.ys);
            }
        });

        if (allX.length === 0) {
            Plotly.restyle(
                spatialPreviewDiv,
                {
                    x: [[]],
                    y: [[]],
                },
                [1],
            );
            currentHighlightedBarcode = null;
            currentHighlightColor = null;
            return;
        }
        
        // Color is already in rgba format from colorWithAlpha, use it directly
        // Plotly accepts rgba strings, so we can pass it directly
        Plotly.restyle(
            spatialPreviewDiv,
            {
                x: [allX],
                y: [allY],
                'marker.color': colorStr,
            },
            [1],
        );
    }
    
    // Convert Plotly data coordinates to canvas pixel coordinates
    function plotlyToPixelCoords(plotlyX, plotlyY) {
        if (!spatialPreviewPlotInstance || !spatialPreviewDiv || !image) return null;
        
        try {
            const rect = spatialPreviewDiv.getBoundingClientRect();
            const width = rect.width;
            const height = rect.height;
            
            const imgW = image.width;
            const imgH = image.height;
            const canvasAspect = width / height;
            const imageAspect = imgW / imgH;
            
            let scale, offsetX = 0, offsetY = 0;
            
            if (canvasAspect > imageAspect) {
                // Canvas is wider, fit by height
                scale = height / imgH;
                offsetX = (width - imgW * scale) / 2;
            } else {
                // Canvas is taller, fit by width
                scale = width / imgW;
                offsetY = (height - imgH * scale) / 2;
            }
            
            // Convert Plotly coordinates to pixel coordinates
            // x: Plotly uses [0, imgW], canvas uses [offsetX, offsetX + imgW*scale]
            const pixelX = offsetX + plotlyX * scale;
            
            // y: Plotly yaxis range is [imgH, 0] (reversed)
            //    - Data y=0 (image top) is displayed at Plotly's bottom (yaxis position 0)
            //    - Data y=imgH (image bottom) is displayed at Plotly's top (yaxis position imgH)
            //    In canvas: y=0 is top, y=height is bottom
            //    To match Plotly's visual position, we need to flip:
            //    - plotlyY=0 (displayed at bottom) -> canvas bottom = offsetY + imgH*scale
            //    - plotlyY=imgH (displayed at top) -> canvas top = offsetY
            //    Formula: pixelY = offsetY + (imgH - plotlyY) * scale
            //    This flips the y coordinate to match Plotly's reversed yaxis display
            const pixelY = offsetY + ( plotlyY) * scale;
            
            return { x: pixelX, y: pixelY };
        } catch (err) {
            console.error("[ClusterResultComparison] Error converting coordinates:", err);
            return null;
        }
    }
    
    function drawPieChart(barcode, x, y) {
        if (!pieChartCanvas || !barcode) {
            // Clear pie chart
            if (pieChartCanvas) {
                const ctx = pieChartCanvas.getContext("2d");
                if (ctx) {
                    ctx.clearRect(0, 0, pieChartCanvas.width, pieChartCanvas.height);
                }
            }
            pieChartBarcode = null;
            pieChartMultiBarcodes = null;
            return;
        }
        
        const distribution = barcodeClusterDistribution.get(`${barcode}`.trim());
        if (!distribution || distribution.size === 0) {
            // Clear pie chart if no distribution
            const ctx = pieChartCanvas.getContext("2d");
            if (ctx) {
                ctx.clearRect(0, 0, pieChartCanvas.width, pieChartCanvas.height);
            }
            pieChartBarcode = null;
            return;
        }
        
        pieChartBarcode = barcode;
        pieChartPosition = { x, y };
        pieChartMultiBarcodes = null;
        
        // Ensure canvas has correct size
        const rect = pieChartCanvas.getBoundingClientRect();
        const dpr = typeof window !== "undefined" ? window.devicePixelRatio || 1 : 1;
        const physicalWidth = Math.floor(rect.width * dpr);
        const physicalHeight = Math.floor(rect.height * dpr);
        
        if (pieChartCanvas.width !== physicalWidth || pieChartCanvas.height !== physicalHeight) {
            pieChartCanvas.width = physicalWidth;
            pieChartCanvas.height = physicalHeight;
        }
        pieChartCanvas.style.width = `${rect.width}px`;
        pieChartCanvas.style.height = `${rect.height}px`;
        
        const ctx = pieChartCanvas.getContext("2d");
        if (!ctx) return;
        
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.clearRect(0, 0, physicalWidth, physicalHeight);
        ctx.scale(dpr, dpr);
        
        // Explicitly disable stroke to ensure no border
        ctx.lineWidth = 0;
        ctx.strokeStyle = "transparent";
        // Disable image smoothing for crisp edges
        // ctx.imageSmoothingEnabled = false;
        
        // 动态计算半径，随容器大小变化
        // 与「展示所有饼图」时普通饼图的大小完全一致
        const { radius: baseRadius } = calculatePieChartRadius();
        const radius = baseRadius;
        const centerX = x;
        const centerY = y;
        
        // Calculate total count
        let total = 0;
        const entries = [];
        distribution.forEach(({ count, color }, cluster) => {
            total += count;
            entries.push({ cluster, count, color });
        });
        
        if (total === 0) return;
        
        // Sort by count descending
        entries.sort((a, b) => b.count - a.count);
        
        // Draw pie chart
        let currentAngle = -Math.PI / 2; // Start from top
        
        entries.forEach(({ count, color }) => {
            const sliceAngle = (count / total) * 2 * Math.PI;
            
            // Draw slice without border
            ctx.beginPath();
            ctx.moveTo(centerX, centerY);
            ctx.arc(centerX, centerY, radius, currentAngle, currentAngle + sliceAngle);
            ctx.closePath();
            ctx.fillStyle = color || "rgba(148, 163, 184, 0.7)";
            ctx.fill();
            // Explicitly do not stroke to avoid any border
            
            currentAngle += sliceAngle;
        });
    }

    // Draw pie charts for a list of barcodes (used for cluster/trend hover on Sankey)
    function drawPieChartsForBarcodes(barcodes) {
        if (showAllPieCharts) return;
        if (!pieChartCanvas || !spatialPreviewBarcodePoints || !barcodeClusterDistribution) {
            return;
        }

        const normalized = Array.isArray(barcodes)
            ? barcodes
                  .map((bc) => (bc === null || bc === undefined ? "" : `${bc}`.trim()))
                  .filter((bc) => bc.length > 0)
            : [];

        if (normalized.length === 0) {
            const ctx = pieChartCanvas.getContext("2d");
            if (ctx) ctx.clearRect(0, 0, pieChartCanvas.width, pieChartCanvas.height);
            pieChartMultiBarcodes = null;
            return;
        }

        // Mark multi mode as active so reactive clearing doesn't wipe the canvas
        pieChartMultiBarcodes = normalized;
        pieChartBarcode = null;

        // Ensure canvas has correct size
        const rect = pieChartCanvas.getBoundingClientRect();
        const dpr = typeof window !== "undefined" ? window.devicePixelRatio || 1 : 1;
        const physicalWidth = Math.floor(rect.width * dpr);
        const physicalHeight = Math.floor(rect.height * dpr);

        if (pieChartCanvas.width !== physicalWidth || pieChartCanvas.height !== physicalHeight) {
            pieChartCanvas.width = physicalWidth;
            pieChartCanvas.height = physicalHeight;
        }
        pieChartCanvas.style.width = `${rect.width}px`;
        pieChartCanvas.style.height = `${rect.height}px`;

        const ctx = pieChartCanvas.getContext("2d");
        if (!ctx) return;

        ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.clearRect(0, 0, physicalWidth, physicalHeight);
        ctx.scale(dpr, dpr);

        // Disable stroke
        ctx.lineWidth = 0;
        ctx.strokeStyle = "transparent";

        // 动态计算半径，随容器大小变化
        const { radius } = calculatePieChartRadius();

        let drawn = 0;
        for (const barcode of normalized) {
            const entry = spatialPreviewBarcodePoints.get(barcode);
            if (!entry || !entry.xs || entry.xs.length === 0) continue;

            const distribution = barcodeClusterDistribution.get(`${barcode}`.trim());
            if (!distribution || distribution.size === 0) continue;

            // Pre-build sorted slices (stable per barcode)
            let total = 0;
            const slices = [];
            distribution.forEach(({ count, color }, cluster) => {
                total += count;
                slices.push({ cluster, count, color });
            });
            if (total === 0) continue;
            slices.sort((a, b) => b.count - a.count);

            const nLoc = Math.min(entry.xs.length, entry.ys.length);
            for (let i = 0; i < nLoc; i += 1) {
                if (drawn >= MAX_HOVER_PIES) return;
                const plotlyX = entry.xs[i];
                const plotlyY = entry.ys[i];
                const pixelCoords = plotlyToPixelCoords(plotlyX, plotlyY);
                if (!pixelCoords) continue;

                const centerX = pixelCoords.x;
                const centerY = pixelCoords.y;

                let currentAngle = -Math.PI / 2;
                slices.forEach(({ count, color }) => {
                    const sliceAngle = (count / total) * 2 * Math.PI;
                    ctx.beginPath();
                    ctx.moveTo(centerX, centerY);
                    ctx.arc(
                        centerX,
                        centerY,
                        radius,
                        currentAngle,
                        currentAngle + sliceAngle,
                    );
                    ctx.closePath();
                    ctx.fillStyle = color || "rgba(148, 163, 184, 0.7)";
                    ctx.fill();
                    currentAngle += sliceAngle;
                });

                drawn += 1;
            }
        }
    }

    function scheduleDrawPieChartsForBarcodes(barcodes) {
        pieChartMultiPending = barcodes;
        if (pieChartMultiRaf) return;
        pieChartMultiRaf = window.requestAnimationFrame(() => {
            pieChartMultiRaf = 0;
            drawPieChartsForBarcodes(pieChartMultiPending);
        });
    }
    
    // Draw pie charts for all spots, optionally highlighting a specific barcode
    function drawAllPieCharts(highlightBarcode = null) {
        if (!pieChartCanvas || !spatialPreviewBarcodePoints || !barcodeClusterDistribution) {
            return;
        }
        
        // Clear canvas first
        const rect = pieChartCanvas.getBoundingClientRect();
        const dpr = typeof window !== "undefined" ? window.devicePixelRatio || 1 : 1;
        const physicalWidth = Math.floor(rect.width * dpr);
        const physicalHeight = Math.floor(rect.height * dpr);
        
        if (pieChartCanvas.width !== physicalWidth || pieChartCanvas.height !== physicalHeight) {
            pieChartCanvas.width = physicalWidth;
            pieChartCanvas.height = physicalHeight;
        }
        pieChartCanvas.style.width = `${rect.width}px`;
        pieChartCanvas.style.height = `${rect.height}px`;
        
        const ctx = pieChartCanvas.getContext("2d");
        if (!ctx) return;
        
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.clearRect(0, 0, physicalWidth, physicalHeight);
        ctx.scale(dpr, dpr);
        
        // Disable stroke
        ctx.lineWidth = 0;
        ctx.strokeStyle = "transparent";
        
        // 动态计算半径，随容器大小变化
        const { radius } = calculatePieChartRadius();
        const normalizedHighlight = highlightBarcode ? `${highlightBarcode}`.trim() : null;
        
        // Draw pie chart for each barcode that has distribution data
        spatialPreviewBarcodePoints.forEach((entry, barcode) => {
            const normalizedBarcode = `${barcode}`.trim();
            const distribution = barcodeClusterDistribution.get(normalizedBarcode);
            if (!distribution || distribution.size === 0) return;
            
            const isHighlighted = normalizedHighlight && normalizedBarcode === normalizedHighlight;
            // 所有饼图大小一致，只通过透明度区分高亮
            const currentRadius = radius;
            // 有高亮时，非高亮饼图降低透明度
            const opacity = normalizedHighlight ? (isHighlighted ? 1 : 0.15) : 1;
            
            // Draw pie chart at each location of this barcode
            entry.xs.forEach((plotlyX, idx) => {
                const plotlyY = entry.ys[idx];
                if (plotlyX === undefined || plotlyY === undefined) return;
                
                const pixelCoords = plotlyToPixelCoords(plotlyX, plotlyY);
                if (!pixelCoords) return;
                
                const centerX = pixelCoords.x;
                const centerY = pixelCoords.y;
                
                // Calculate total count
                let total = 0;
                const entries = [];
                distribution.forEach(({ count, color }, cluster) => {
                    total += count;
                    entries.push({ cluster, count, color });
                });
                
                if (total === 0) return;
                
                // Sort by count descending
                entries.sort((a, b) => b.count - a.count);
                
                // Draw pie chart
                let currentAngle = -Math.PI / 2; // Start from top
                
                ctx.globalAlpha = opacity;
                entries.forEach(({ count, color }) => {
                    const sliceAngle = (count / total) * 2 * Math.PI;
                    
                    ctx.beginPath();
                    ctx.moveTo(centerX, centerY);
                    ctx.arc(centerX, centerY, currentRadius, currentAngle, currentAngle + sliceAngle);
                    ctx.closePath();
                    ctx.fillStyle = color || "rgba(148, 163, 184, 0.7)";
                    ctx.fill();
                    
                    currentAngle += sliceAngle;
                });
                ctx.globalAlpha = 1;
            });
        });
    }


    // teardownScrollSync and setupScrollSync moved to sankeyDiagram.svelte component

    // hitTestCluster, hitTestConnection, updateTooltipPosition, handlePointerMove, handlePointerLeave, handleSankeyClick, handleSankeyDoubleClick moved to sankeyDiagram.svelte component
    // Reset to aggregated mode
    function handleResetToAggregated() {
        // flowMode = "aggregated";
        focusedClusterByRow = {};
        activeFocus = null;
        activeFocusRank = null;
        // scheduleMeasure moved to sankeyDiagram.svelte component
    }

    onMount(() => {
        // Resize handling and ResizeObserver moved to sankeyDiagram.svelte component
        return () => {
            // Cleanup moved to sankeyDiagram.svelte component
        };
    });

    onDestroy(() => {
        if (spatialPreviewPlotInstance && spatialPreviewDiv) {
            Plotly.purge(spatialPreviewDiv);
            spatialPreviewPlotInstance = null;
        }
        if (spatialPreviewResizeObserver) {
            spatialPreviewResizeObserver.disconnect();
            spatialPreviewResizeObserver = null;
        }
        // 清理防抖 timeout
        if (drawSpatialPreviewTimeout) {
            clearTimeout(drawSpatialPreviewTimeout);
            drawSpatialPreviewTimeout = null;
        }
        if (drawAllPieChartsTimeout) {
            clearTimeout(drawAllPieChartsTimeout);
            drawAllPieChartsTimeout = null;
        }
        // teardownScrollSync and resizeObserver cleanup moved to sankeyDiagram.svelte component
        // radarResizeObserver and metricsResizeObserver are handled by MetricsComparison component
    });

    // Reactive statements for Sankey diagram moved to sankeyDiagram.svelte component
    
    // 当空间预览数据、底图和div就绪时绘制Plotly图表
    $: if (spatialPreviewDiv && spatialData && spatialData.length && image) {
        drawSpatialPreviewPlot();
    }
    
    // 当distributionSummary更新后（新增聚类结果时），确保spatial preview也重绘
    $: if (visibleDistributionSummary?.length > 0 && spatialPreviewDiv && spatialData && spatialData.length && image) {
        tick().then(() => {
            // 延迟一点确保数据已完全加载，使用防抖避免重复调用
            debouncedDrawSpatialPreviewPlot(200);
        });
    }
    
    // Redraw spatial preview when stability threshold changes to apply filtering
    // 用独立变量跟踪 maxStabilityPercent 变化，避免依赖 barcodeClusterDistribution 导致级联触发
    let prevMaxStabilityPercent = maxStabilityPercent;
    $: if (maxStabilityPercent !== prevMaxStabilityPercent) {
        prevMaxStabilityPercent = maxStabilityPercent;
        if (spatialPreviewDiv && spatialData && spatialData.length && image) {
            debouncedDrawSpatialPreviewPlot(150);
            if (showAllPieCharts && pieChartCanvas) {
                debouncedDrawAllPieCharts(allPieChartsHighlightBarcode, 200);
            }
        }
    }
    

    // Initialize pie chart canvas size
    $: if (pieChartCanvas && spatialPreviewDiv) {
        const rect = spatialPreviewDiv.getBoundingClientRect();
        const dpr = typeof window !== "undefined" ? window.devicePixelRatio || 1 : 1;
        const physicalWidth = Math.floor(rect.width * dpr);
        const physicalHeight = Math.floor(rect.height * dpr);
        
        if (pieChartCanvas.width !== physicalWidth || pieChartCanvas.height !== physicalHeight) {
            pieChartCanvas.width = physicalWidth;
            pieChartCanvas.height = physicalHeight;
        }
        pieChartCanvas.style.width = `${rect.width}px`;
        pieChartCanvas.style.height = `${rect.height}px`;
    }
    
    // Rebuild barcode cluster distribution when visibleDistributionSummary changes
    $: if (visibleDistributionSummary?.length > 0) {
        buildBarcodeClusterDistribution();
    }
    
    // Redraw pie chart when canvas size changes or barcode changes
    $: if (pieChartCanvas && pieChartBarcode && pieChartPosition && !showAllPieCharts) {
        // Redraw pie chart if barcode is set (only when not showing all pie charts)
        drawPieChart(pieChartBarcode, pieChartPosition.x, pieChartPosition.y);
    }
    
    // Draw all pie charts when switch is enabled - 使用防抖避免级联触发
    let prevShowAllPieCharts = showAllPieCharts;
    $: if (showAllPieCharts !== prevShowAllPieCharts) {
        prevShowAllPieCharts = showAllPieCharts;
        if (showAllPieCharts && pieChartCanvas && spatialPreviewBarcodePoints && spatialPreviewBarcodePoints.size > 0 && barcodeClusterDistribution && barcodeClusterDistribution.size > 0 && spatialPreviewDiv && image) {
            tick().then(() => {
                debouncedDrawAllPieCharts(allPieChartsHighlightBarcode, 150);
                ensureTooltipAbovePieCanvas();
            });
        }
    }

    // Redraw all pie charts when spatial preview plot instance changes (if switch is enabled)
    let prevSpatialPreviewPlotInstance = spatialPreviewPlotInstance;
    $: if (spatialPreviewPlotInstance !== prevSpatialPreviewPlotInstance) {
        prevSpatialPreviewPlotInstance = spatialPreviewPlotInstance;
        if (showAllPieCharts && spatialPreviewPlotInstance && spatialPreviewBarcodePoints && spatialPreviewBarcodePoints.size > 0 && barcodeClusterDistribution && barcodeClusterDistribution.size > 0) {
            tick().then(() => {
                debouncedDrawAllPieCharts(allPieChartsHighlightBarcode, 200);
                ensureTooltipAbovePieCanvas();
            });
        }
    }
    
    // Clear all pie charts when switch is disabled and there is no active hover pie chart
    // (avoid clearing right after drawPieChart on hover)
    $: if (!showAllPieCharts && pieChartCanvas && !pieChartBarcode && !pieChartMultiBarcodes) {
        const ctx = pieChartCanvas.getContext("2d");
        if (ctx) {
            ctx.clearRect(0, 0, pieChartCanvas.width, pieChartCanvas.height);
        }
    }
    
    // Note: Pie chart clearing is handled in handlePointerMove when not hovering
    // and in drawPieChart when barcode is null. No need for a reactive statement here
    // that would interfere with hover functionality.
    
    // 监听空间预览容器大小变化，自动调整Plotly图表和饼图大小
    $: if (spatialPreviewDiv && typeof ResizeObserver !== "undefined") {
        if (spatialPreviewResizeObserver) {
            spatialPreviewResizeObserver.disconnect();
        }
        spatialPreviewResizeObserver = new ResizeObserver(() => {
            if (spatialPreviewPlotInstance && spatialPreviewDiv) {
                Plotly.Plots.resize(spatialPreviewDiv);
                // 容器大小变化后重绘饼图，保持饼图大小与 spot 一致
                if (showAllPieCharts) {
                    debouncedDrawAllPieCharts(allPieChartsHighlightBarcode, 100);
                }
            }
        });
        spatialPreviewResizeObserver.observe(spatialPreviewDiv);
    }
    
    // Prevent layout-only changes from triggering data reloads (see comparisonKey reactive block).
    let isLayoutChangeInProgress = false;
    let previousLeftPanelCollapsed = isLeftPanelCollapsed;
    $: if (isLeftPanelCollapsed !== previousLeftPanelCollapsed) {
        previousLeftPanelCollapsed = isLeftPanelCollapsed;
        isLayoutChangeInProgress = true;
        setTimeout(() => {
            isLayoutChangeInProgress = false;
        }, 200);
    }
    

    // Scroll sync and ResizeObserver reactive statements moved to sankeyDiagram.svelte component
</script>

<div class="flex w-full flex-col gap-3" style="height: 100%; min-height: 0; max-height: 100%;">
    <div
        class="flex flex-col rounded-xl border border-slate-200 bg-white/95 p-4 shadow-sm"
        style="height: 100%; min-height: 0; max-height: 100%; flex: 1 1 0;"
    >
        <header class="mb-3 flex items-center justify-between flex-shrink-0">
            <div class="flex items-center gap-2">
                <div class="flex items-center gap-1.5">
                    <h2
                        class="text-sm font-semibold uppercase tracking-wide text-slate-600"
                    >
                        Cluster Comparison
                    </h2>
                    <span
                        class="inline-flex items-center justify-center text-slate-400 hover:text-slate-600 cursor-help relative"
                        aria-label="Cluster Comparison help"
                        role="button"
                        tabindex="0"
                        on:mouseenter={() => (showClusterComparisonTooltip = true)}
                        on:mouseleave={() => (showClusterComparisonTooltip = false)}
                    >
                        <Info size={14} />
                        {#if showClusterComparisonTooltip}
                            <div
                                class="absolute z-30 mt-20 left-5 w-72 max-w-[70vw] text-[11px] leading-snug text-slate-50 bg-slate-900/95 rounded-md shadow-lg px-2.5 py-1.5"
                            >
                                The Sankey diagram visualizes how spots are distributed across different clustering results. Each row represents a clustering result, with colored bars showing clusters. The flow connections between rows illustrate how spots transition between clusters across different results, helping identify consistent patterns and variations in clustering outcomes.
                            </div>
                        {/if}
                    </span>
                </div>
                <span class="text-xs text-slate-500">
                    {#if flowMode === "aggregated"}
                        Click to view individual spots
                    {:else}
                        Double-click to navigate to cluster details
                    {/if}
                </span>
                {#if flowMode === "individual" && Object.keys(focusedClusterByRow).length > 0}
                    <button
                        type="button"
                        class="inline-flex items-center gap-1 h-6 px-2 rounded border border-slate-300 bg-slate-50 text-[11px] font-medium text-slate-600 hover:bg-slate-100 transition-colors focus:outline-none focus:ring-2 focus:ring-slate-400"
                        on:click={handleResetToAggregated}
                        title="Reset to aggregated view"
                    >
                        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                        </svg>
                        Reset
                    </button>
                {/if}
            </div>
            <div class="flex items-center gap-3">
                {#if clusterResults?.length && visibleDistributionSummary?.length > 0}
                    <label class="flex items-center gap-2">
                        <div class="flex items-center gap-1.5">
                            <span class="text-[11px] text-slate-500 whitespace-nowrap">
                                Max stability
                            </span>
                            <span
                                class="inline-flex items-center justify-center text-slate-400 hover:text-slate-600 cursor-help relative"
                                aria-label="Max stability help"
                                role="button"
                                tabindex="0"
                                on:mouseenter={() => (showMaxStabilityTooltip = true)}
                                on:mouseleave={() => (showMaxStabilityTooltip = false)}
                            >
                                <Info size={12} />
                                {#if showMaxStabilityTooltip}
                                    <div
                                        class="absolute z-30 mt-10 left-5 w-64 max-w-[70vw] text-[11px] leading-snug text-slate-50 bg-slate-900/95 rounded-md shadow-lg px-2.5 py-1.5"
                                    >
                                        Filters out barcodes with stability greater than the selected percentage. Stability measures the consistency of spots across different clustering results.
                                    </div>
                                {/if}
                            </span>
                        </div>
                        <input
                            type="range"
                            min="0"
                            max="100"
                            step={visibleDistributionSummary.length > 0 ? 100 / visibleDistributionSummary.length : 1}
                            bind:value={maxStabilityPercent}
                            class="w-32 cursor-pointer"
                            style="accent-color: var(--color-slate-600, var(--color-primary-600));"
                        />
                        <span class="text-[11px] text-slate-500 tabular-nums w-12 text-right">
                            {Math.round(maxStabilityPercent)}%
                        </span>
                    </label>
                    <label class="flex items-center gap-2 cursor-pointer">
                        <div class="flex items-center gap-1.5">
                            <span class="text-[11px] text-slate-500">
                                Show all pie charts
                            </span>
                            <span
                                class="inline-flex items-center justify-center text-slate-400 hover:text-slate-600 cursor-help relative"
                                aria-label="Show all pie charts help"
                                role="button"
                                tabindex="0"
                                on:mouseenter={() => (showAllPieChartsTooltip = true)}
                                on:mouseleave={() => (showAllPieChartsTooltip = false)}
                            >
                                <Info size={12} />
                                {#if showAllPieChartsTooltip}
                                    <div
                                        class="absolute z-30 mt-10 right-5 w-64 max-w-[70vw] text-[11px] leading-snug text-slate-50 bg-slate-900/95 rounded-md shadow-lg px-2.5 py-1.5"
                                    >
                                        When enabled, displays pie charts for all spots on the spatial preview, showing the cluster distribution across different clustering results. When disabled, pie charts are only shown when hovering over spots.
                                    </div>
                                {/if}
                            </span>
                        </div>
                        <input
                            type="checkbox"
                            bind:checked={showAllPieCharts}
                            class="w-4 h-4 rounded border-slate-300 text-slate-600 focus:ring-2 focus:ring-slate-400 cursor-pointer"
                        />
                    </label>
                    <!-- Segment and Flow selectors hidden -->
                    <label class="hidden flex items-center gap-2">
                        <span class="text-[11px] text-slate-500 whitespace-nowrap">
                            Segment
                        </span>
                        <select
                            bind:value={segmentMode}
                            class="h-7 rounded border border-slate-200 bg-white px-2 text-[11px] text-slate-600 focus:outline-none focus:ring-2 focus:ring-slate-400"
                        >
                            <option value="cluster_only">Cluster only</option>
                            <option value="next_cluster">Next cluster</option>
                            <option value="cluster_set">Cluster set</option>
                            <option value="trend_sequence">Trend sequence</option>
                        </select>
                    </label>
                    <label class="hidden flex items-center gap-2">
                        <span class="text-[11px] text-slate-500 whitespace-nowrap">
                            Flow
                        </span>
                        <select
                            bind:value={flowMode}
                            class="h-7 rounded border border-slate-200 bg-white px-2 text-[11px] text-slate-600 focus:outline-none focus:ring-2 focus:ring-slate-400"
                        >
                            <option value="aggregated">Aggregated</option>
                            <option value="individual">Individual</option>
                        </select>
                    </label>
                    <!-- <span class="text-[11px] text-slate-500">
                        {clusterResults.length}
                        {clusterResults.length === 1 ? "result" : "results"}
                    </span> -->
                {:else if clusterResults?.length}
                    <span class="text-[11px] text-slate-500">
                        {clusterResults.length}
                        {clusterResults.length === 1 ? "result" : "results"}
                    </span>
                {/if}
            </div>
        </header>
        <div class="flex flex-col flex-1 min-h-0" style="height: calc(50vh - 60px); min-height: 0;">
            {#if loading}
            <!-- <div
                class="flex flex-1 items-center justify-center rounded-xl border border-dashed border-slate-200 bg-white text-sm text-slate-500"
            >
                <span
                    class="mr-2 inline-flex h-5 w-5 animate-spin rounded-full border-2 border-slate-300 border-t-slate-500"
                    aria-hidden="true"
                ></span>
                Loading cluster comparisons…
            </div> -->
        {:else if error}
            <div
                class="flex flex-1 items-center justify-center rounded-xl border border-rose-200 bg-rose-50 px-4 py-6 text-sm text-rose-600"
            >
                {error}
            </div>
        {:else if !clusterResults?.length}
            <div
                class="flex flex-1 items-center justify-center rounded-xl border border-dashed border-slate-200 bg-white px-4 py-6 text-sm text-slate-500"
            >
                No cluster results available yet. Run a clustering job to enable
                comparisons.
            </div>
        {:else if !visibleDistributionSummary.length}
            <div
                class="flex flex-1 items-center justify-center rounded-xl border border-dashed border-slate-200 bg-white px-4 py-6 text-sm text-slate-500"
            >
                No cluster statistics were returned for the selected results.
            </div>
        {:else}
            <div class="relative flex flex-1 min-h-0 min-w-0 gap-3 flex-1" style="flex: 1 1 0; min-height: 0;">
                <!-- Sankey diagram on the left -->
                <SankeyDiagramVertical
                    {visibleDistributionSummary}
                    {selectedResultId}
                    {verticalLayout}
                    {scrollSource}
                    bind:maxStabilityPercent
                    bind:flowMode
                    bind:segmentMode
                    bind:focusedClusterByRow
                    bind:activeFocus
                    bind:activeFocusRank
                    bind:clusterLayoutInfo
                    bind:sankeyDimensions
                    {highlightedBarcodeFromPreview}
                    {barcodeClusterDistribution}
                    {barcodeStabilityPercent}
                    clusterMetricsData={clusterMetricsData}
                    on:highlightBarcode={(e) => {
                        highlightedBarcodeFromSankey = e.detail.barcode;
                        if (e.detail.barcode) {
                            // Sankey hover 不再使用橙色 Plotly 点，只用饼图表示高亮
                            if (!showAllPieCharts) {
                                const entry = spatialPreviewBarcodePoints.get(e.detail.barcode);
                                if (entry && entry.xs.length > 0 && entry.ys.length > 0) {
                                    const plotlyX = entry.xs[0];
                                    const plotlyY = entry.ys[0];
                                    const pixelCoords = plotlyToPixelCoords(plotlyX, plotlyY);
                                    if (pixelCoords) {
                                        drawPieChart(e.detail.barcode, pixelCoords.x, pixelCoords.y);
                                    }
                                }
                            } else {
                                // 展示所有饼图时，通过饼图透明度/大小高亮
                                allPieChartsHighlightBarcode = e.detail.barcode;
                                drawAllPieCharts(e.detail.barcode);
                            }
                        } else {
                            if (!showAllPieCharts) {
                                // 清理 Sankey 触发的饼图高亮
                                drawPieChart(null, 0, 0);
                            } else {
                                allPieChartsHighlightBarcode = null;
                                drawAllPieCharts(null);
                            }
                        }
                    }}
                    on:highlightBarcodes={(e) => {
                        // Sankey 柱子 hover 时只画饼图，不画橙色高亮点
                        if (!showAllPieCharts) {
                            if (e.detail.barcodes && e.detail.barcodes.length > 0) {
                                scheduleDrawPieChartsForBarcodes(e.detail.barcodes);
                            } else {
                                drawPieChart(null, 0, 0);
                                pieChartMultiBarcodes = null;
                            }
                        }
                    }}
                    on:clusterExpand={(e) => {
                        flowMode = e.detail.flowMode;
                        focusedClusterByRow = e.detail.focusedClusterByRow;
                        activeFocus = e.detail.activeFocus;
                        activeFocusRank = e.detail.activeFocusRank;
                    }}
                    on:flowSegmentClick={(e) => {
                        dispatch("flowSegmentClick", e.detail);
                    }}
                    on:selectClusterResult={(e) => {
                        dispatch("selectClusterResult", e.detail);
                    }}
                    on:highlightedBarcodeChange={(e) => {
                        // This event is already handled by highlightBarcode event above
                        // But we keep it for consistency
                    }}
                />
                <!-- Spatial preview (slice-level scatter plot) on the right -->
                <div 
                    class="flex-shrink-0 flex items-center justify-center overflow-hidden relative"
                    style="height: 100%; aspect-ratio: {image ? `${image.width} / ${image.height}` : '1 / 1'}; max-width: 100%;"
                >
                    <!-- chartDiv 不设 position: relative，避免创建新 stacking context，这样 hoverlayer 的 z-index 才能超出 -->
                    <div
                        bind:this={spatialPreviewDiv}
                        class="w-full h-full"
                        style="min-width: 0; min-height: 0;"
                    ></div>
                    <!-- Pie chart overlay canvas (z-index: 1 与下游分析一致，hoverlayer 用 JS 设为更高值 20) -->
                    <canvas
                        bind:this={pieChartCanvas}
                        class="absolute inset-0 w-full h-full"
                        style="z-index: 1; pointer-events: none !important;"
                    ></canvas>
                </div>
            </div>
            
            <!-- Circular comparison (replaces MetricsComparison section) -->
            {#if visibleDistributionSummary?.length > 0}
                <LabelResultClusterSpotCircular
                    {visibleDistributionSummary}
                    {selectedResultId}
                    {metricsData}
                    {clusterMetricsData}
                    {sankeyDimensions}
                    bind:isCollapsed={isBelowCollapsed}
                />
            {/if}
        {/if}
        </div>
    </div>
    
    <!-- Tooltip is now rendered inside SankeyDiagram component -->
</div>

<style>
    /* 空间坐标图 tooltip 显示在饼图之上（与下游分析 downstreamAnalysis 一致） */
    :global(.plotly .infolayer) {
        z-index: 9999 !important;
        pointer-events: none !important;
    }

    :global(.plotly .hoverlayer) {
        z-index: 10000 !important;
        pointer-events: auto !important;
    }

    :global(.plotly) {
        position: relative;
        z-index: 1;
    }

    :global(.plotly svg) {
        position: relative;
        z-index: 2;
    }
</style>
