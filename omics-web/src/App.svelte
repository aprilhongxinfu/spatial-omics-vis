<script>
    import { onMount, tick, onDestroy } from "svelte";
    import Plotly from "plotly.js-dist-min";
    import { ChevronLeft, ChevronRight, X, ExternalLink, Plus } from "@lucide/svelte";
    import * as d3 from "d3";
    import Split from "split.js";
    import Plot from "./component/plot.svelte";
    import OverviewUmapPanel from "./component/overviewUmapPanel.svelte";
    import OverviewClusterPanel from "./component/overviewClusterPanel.svelte";
    import ClusterSpotLogPanel from "./component/clusterSpotLogPanel.svelte";
    import DownstreamAnalysis from "./component/downstreamAnalysis.svelte";
    import { ProgressRing } from "@skeletonlabs/skeleton-svelte";
    import Lassomode from "./component/lassomode.svelte";
    import ClusterManagement from "./component/clusterManagement.svelte";
    import StatisticsPanel from "./component/statisticsPanel.svelte";
    import ClusterResultComparison from "./component/clusterResultComparison.svelte";
    import SankeySortingDemo from "./component/sankeySortingDemo.svelte";
    import { load } from "three/examples/jsm/libs/opentype.module.js";

    const baseApi = "/api";
    let imageUrl;
    const clusteringMethods = ["GraphST", "SEDR", "SpaGCN"];
    const defaultColorScaleBase = d3
        .scaleOrdinal(d3.schemeTableau10)
        .unknown("#999999");

    let currentMethod = clusteringMethods[0];
    let uiClusteringMethod = clusteringMethods[0];
    let spatialDiv, heatmapDiv;
    let clickedInfo;
    let spatialData;
    let spatialInfo;
    let expandedIndex = null;

    let availableClusters;

    // Split.js variables
    let splitContainer;
    let topPanel;
    let bottomPanel;
    let splitInstance = null;

    let allSlices, currentSlice, prevSlice;
    let ncountSpatialData, spotMetricsData;
    let clusterDistribution = [];
    let clusterColorScale = defaultColorScaleBase.copy();
    let clusterColorScaleVersion = 0;

    function normalizeClusterName(name) {
        return `${name ?? ""}`
            .trim()
            .replace(/^cluster\s+/i, "")
            .replace(/^cluster_/i, "")
            .trim();
    }

    function buildClusterColorScale(names = [], colorMapping = {}) {
        const domain = [];
        const range = [];
        const baseNames =
            names && names.length
                ? names
                : availableClusters && availableClusters.length
                  ? availableClusters
                  : [];

        baseNames.forEach((name, idx) => {
            const normalized = normalizeClusterName(name);
            if (!normalized) return;

            const color =
                colorMapping[normalized] ??
                colorMapping[name] ??
                defaultColorScaleBase(normalized || `${idx}`);

            const variants = [
                normalized,
                `${normalized}`,
                `Cluster ${normalized}`,
                `cluster ${normalized}`,
            ];

            variants.forEach((variant) => {
                const key = `${variant ?? ""}`.trim();
                if (!key) return;
                if (!domain.includes(key)) {
                    domain.push(key);
                    range.push(color);
                }
            });
        });

        if (domain.length) {
            clusterColorScale = d3
                .scaleOrdinal()
                .domain(domain)
                .range(range)
                .unknown("#999999");
        } else {
            clusterColorScale = defaultColorScaleBase.copy();
        }
        clusterColorScaleVersion += 1;
    }

    function setDefaultClusterColorScale(names = []) {
        buildClusterColorScale(names);
    }

    let hvg;
    let allLog;
    let lassoSelected = false;
    let reclusering = false;
    let reclustered = false;
    let hoveredBarcode = { barcode: "", from: "", persistent: false };
    let lockedBarcode = null;
    let lassoClearSignal = 0;
    let epoch = 500;
    let n_clusters = 7;
    let umapData;
    let cellChat;
    let allClustersMarkerGenes = {}; // Cached marker genes for all clusters
    let cellTypeAnnotations = null; // Cell type annotations for clusters (null = not loaded yet, {} = loaded)
    let lassoHover;
    let clusterGeneExpression;
    let clusterGeneDot;
    let selectedClusterForGenes = null;
    let clusterTopGenes = null;
    let isLoadingClusterGenes = false;
    let image;
    let loading = true;
    let previewUrl = "";
    let currentClusterResultId = "default";
    let currentClusterResult = null;
    let isLoadingClusterResult = false;
    let isInitializing = false;
    let lastColorMappingKey = null;
    let loadingTimeoutId = null; // Timeout ID for fallback loading cancellation
    let clusterResultsList = [];
    let comparisonModeActive = true;
    let prevComparisonModeActive = comparisonModeActive;

    // 子组件加载状态：聚类结果列表 & 比较视图
    let isClusterResultsLoading = false;
    let isComparisonLoading = false;

    // Sorting
    let sortBy = "created_at"; // Default sort by creation time
    // sortDirection: "default" | "asc" | "desc"
    // "default": metrics use built-in preference (PAS low→high, others high→low)
    // "asc"/"desc": force ascending / descending for metric-based sorting
    let sortDirection = "default";
    let manualOrder = null; // Manual order array of cluster_result_ids
    
    // Hidden cluster result IDs (Set for efficient lookup)
    let hiddenClusterResultIds = new Set();
    
    // When manualOrder changes, update sortBy to "custom"
    $: if (manualOrder && manualOrder.length > 0 && sortBy !== "custom") {
        sortBy = "custom";
    }
    
    // Module switching
    let currentModule = "clustering"; // Default to clustering module
    let isLeftPanelCollapsed = false;
    const LEFT_PANEL_WIDTH = 360;
    const LEFT_PANEL_MIN_WIDTH = 280;
    const LEFT_PANEL_COLLAPSED_WIDTH = 48;

    // Slice info 固定展示的 label 键（无数据时 value 留空，label 仍显示）
    const SLICE_INFO_LABEL_KEYS = ["spot_count", "gene_count", "avg_genes_per_spot"];
    $: sliceInfoDisplayEntries = (() => {
        const details = spatialInfo?.info_details ?? {};
        const extraKeys = Object.keys(details).filter(
            (k) => !SLICE_INFO_LABEL_KEYS.includes(k) && k !== "expression"
        );
        const keys = [...SLICE_INFO_LABEL_KEYS, ...extraKeys];
        return keys.map((key) => ({
            key,
            label: key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()),
            value: details[key] != null && details[key] !== "" ? details[key] : "",
        }));
    })();

    // Left sidebar collapsible sections
    // Initial state: show Slice info, collapse Cluster result
    let isSliceSectionOpen = true;
    let isClusterSectionOpen = false;
    let showSortingDemo = false;

    $: leftPanelWidth = isLeftPanelCollapsed
        ? LEFT_PANEL_COLLAPSED_WIDTH
        : LEFT_PANEL_WIDTH;
    $: leftPanelMinWidth = isLeftPanelCollapsed
        ? LEFT_PANEL_COLLAPSED_WIDTH
        : LEFT_PANEL_MIN_WIDTH;
    $: leftPanelStyle = `flex: 0 0 ${leftPanelWidth}px; min-width: ${leftPanelMinWidth}px;`;

    $: showDownstreamModule =
        !comparisonModeActive &&
        currentClusterResultId &&
        currentClusterResultId !== "default";

    $: if (!showDownstreamModule && currentModule === "downstream") {
        currentModule = "clustering";
    }

    // Sort clusterResultsList based on manualOrder or sortBy/sortDirection
    // Filter out hidden results first
    $: sortedClusterResultsList = (() => {
        if (clusterResultsList.length === 0) return [];
        
        // Filter out hidden results
        const visibleResults = clusterResultsList.filter(
            result => !hiddenClusterResultIds.has(result.cluster_result_id)
        );
        
        if (visibleResults.length === 0) return [];
        
        // If sortBy is "custom" and manual order exists, use manual order
        if (sortBy === "custom" && manualOrder && manualOrder.length > 0) {
            const orderMap = new Map();
            manualOrder.forEach((id, index) => {
                orderMap.set(id, index);
            });
            return [...visibleResults].sort((a, b) => {
                const indexA = orderMap.get(a.cluster_result_id) ?? 9999;
                const indexB = orderMap.get(b.cluster_result_id) ?? 9999;
                return indexA - indexB;
            });
        }
        
        // If sortBy is "custom" but no manual order, fall back to created_at sorting
        if (sortBy === "custom") {
            return [...visibleResults].sort((a, b) => {
                const timeA = new Date(a.created_at || a.updated_at || 0).getTime();
                const timeB = new Date(b.created_at || b.updated_at || 0).getTime();
                return timeB - timeA; // Newest first
            });
        }
        
        // Otherwise, use sortBy
        return [...visibleResults].sort((a, b) => {
            if (sortBy === "created_at") {
                // Sort by creation time (newest first)
                const timeA = new Date(a.created_at || a.updated_at || 0).getTime();
                const timeB = new Date(b.created_at || b.updated_at || 0).getTime();
                return timeB - timeA; // Newest first
            } else {
                // Sort by metrics (direction controlled by sortDirection; default keeps PAS special rule)
                const metricsA = a.metrics || {};
                const metricsB = b.metrics || {};
                const valueA = metricsA[sortBy];
                const valueB = metricsB[sortBy];

                // Handle null/undefined values
                if (valueA === null || valueA === undefined) return 1; // Move to end
                if (valueB === null || valueB === undefined) return -1; // Move to beginning

                // Determine comparison based on direction
                if (sortDirection === "asc") {
                    return valueA - valueB;
                }
                if (sortDirection === "desc") {
                    return valueB - valueA;
                }
                // default direction: keep existing semantics
                if (sortBy === "pas") {
                    return valueA - valueB; // Lower values first
                }
                return valueB - valueA; // Higher values first
            }
        });
    })();

    const imageCache = new Map();

    async function loadImage(url) {
        if (imageCache.has(url)) return imageCache.get(url);

        const img = await new Promise((resolve) => {
            const newImg = new Image();
            newImg.crossOrigin = "anonymous";
            newImg.src = url;
            newImg.onload = () => resolve(newImg);
        });

        imageCache.set(url, img);
        return img;
    }

    function toggleLeftPanel() {
        isLeftPanelCollapsed = !isLeftPanelCollapsed;
    }

    async function fecthUmapData(
        sliceId = currentSlice,
        clusterResultId = currentClusterResultId,
    ) {
        let url = `${baseApi}/umap-coordinates?slice_id=${sliceId}`;
        if (clusterResultId) {
            url += `&cluster_result_id=${clusterResultId}`;
        }

        const response = await fetch(url);
        const data = await response.json();
        return data;
    }

    // Store the last color mapping for reuse
    let lastColorMapping = {};
    
    // 统一获取服务器返回的聚类颜色映射，并在全局使用
    async function fetchClusterColorMapping(
        sliceId,
        clusterResultId = "default",
        fallbackNames = [],
    ) {
        const cacheKey = `${sliceId}:${clusterResultId}`;
        // Check if we need to rebuild: if clusters don't match, rebuild even if cache key matches
        if (
            cacheKey === lastColorMappingKey &&
            clusterColorScale &&
            typeof clusterColorScale === "function" &&
            availableClusters &&
            availableClusters.length
        ) {
            // Verify that all fallbackNames are in the current color scale domain
            if (fallbackNames && fallbackNames.length > 0) {
                const currentDomain = clusterColorScale.domain();
                const domainSet = new Set(currentDomain);
                const allInDomain = fallbackNames.every(name => {
                    const normalized = normalizeClusterName(name);
                    return domainSet.has(name) || 
                           domainSet.has(normalized) || 
                           domainSet.has(`Cluster ${normalized}`) ||
                           domainSet.has(`cluster ${normalized}`);
                });
                // If all clusters are in domain and lengths match, use cache
                if (allInDomain && fallbackNames.length === currentDomain.length / 4) {
                    // Each cluster has 4 variants in domain, so divide by 4
                    return true;
                }
            } else {
                return true;
            }
        }

        let baseNames = Array.isArray(fallbackNames) ? [...fallbackNames] : [];
        try {
            const res = await fetch(
                `${baseApi}/cluster-color-mapping?slice_id=${sliceId}&cluster_result_id=${clusterResultId}`,
            );
            if (!res.ok) throw new Error("color mapping fetch failed");
            const data = await res.json();

            const clusters = Array.isArray(data.clusters) ? data.clusters : [];
            if (clusters.length) {
                availableClusters = [...clusters];
                baseNames = [...clusters];
            }

            const colorMapping = data.color_mapping || {};
            const mappingKeys = Object.keys(colorMapping);
            if (!baseNames.length && mappingKeys.length) {
                baseNames = [...mappingKeys];
            }
            if (
                !baseNames.length &&
                availableClusters &&
                availableClusters.length
            ) {
                baseNames = [...availableClusters];
            }

            if (baseNames.length || mappingKeys.length) {
                const normalizedMapping = mappingKeys.reduce((acc, key) => {
                    const normalized = normalizeClusterName(key);
                    const rawColor = colorMapping[key];
                    const candidates = new Set([key, normalized]);

                    if (normalized) {
                        const trimmedZero = normalized.replace(/\.0+$/, "");
                        if (trimmedZero) {
                            candidates.add(trimmedZero);
                        }

                        const numericValue = Number.parseFloat(normalized);
                        if (!Number.isNaN(numericValue)) {
                            candidates.add(`${numericValue}`);
                            candidates.add(`${numericValue.toFixed(0)}`);
                        }
                    }

                    candidates.forEach((candidate) => {
                        if (
                            candidate &&
                            candidate !== "undefined" &&
                            candidate !== "null"
                        ) {
                            acc[candidate] = rawColor;
                        }
                    });
                    return acc;
                }, {});

                const combinedNames = Array.from(
                    new Set([...baseNames, ...mappingKeys]),
                );

                buildClusterColorScale(
                    combinedNames.length ? combinedNames : mappingKeys,
                    normalizedMapping,
                );
                
                // Store the color mapping for potential reuse
                lastColorMapping = normalizedMapping;

                if (!clusters.length && combinedNames.length) {
                    availableClusters = [...combinedNames];
                }
            } else {
                setDefaultClusterColorScale(baseNames);
                lastColorMapping = {};
            }
            lastColorMappingKey = cacheKey;
            return true;
        } catch (e) {
            console.warn(
                "Fallback to local color scale due to mapping API error:",
                e,
            );
        }
        setDefaultClusterColorScale(baseNames);
        lastColorMappingKey = null;
        return false;
    }

    async function fetchSpatial() {
        // 先获取所有的切片 ID
        const slicesRes = await fetch(baseApi + "/allslices");
        allSlices = await slicesRes.json();
        if (!currentSlice) {
            currentSlice = allSlices[0];
        }

        // 先让后端加载当前切片并创建 spot_cluster 表，否则 plot-data 会 404/空，spot 不显示
        try {
            const changeRes = await fetch(baseApi + `/changeSlice?sliceid=${currentSlice}`);
            if (!changeRes.ok) {
                const err = await changeRes.json().catch(() => ({}));
                console.warn("changeSlice 未成功，plot 可能为空:", currentSlice, err.detail || changeRes.statusText);
            }
        } catch (e) {
            console.warn("changeSlice 请求失败:", currentSlice, e);
        }

        // 注意：不在函数内部设置 imageUrl，避免触发额外的状态更新
        // 改为返回 imageUrl，让调用者统一设置
        const newImageUrl = `${baseApi}/images/${currentSlice}/tissue_hires_image.png`;

        // const image = new Image();
        // image.src = newImageUrl;
        image = await loadImage(newImageUrl); // ✅ 缓存加载

        // await new Promise((resolve) => (image.onload = resolve));

        // 预先获取 slice-info（新切片可能无 info.json，失败时用默认值，不阻断 spot-metrics 等）
        let sliceInfo;
        try {
            const infoRes = await fetch(
                `${baseApi}/slice-info?slice_id=${currentSlice}`,
            );
            sliceInfo = infoRes.ok ? await infoRes.json() : null;
        } catch (_) {
            sliceInfo = null;
        }
        if (!sliceInfo || typeof sliceInfo !== "object") {
            sliceInfo = {
                info_details: {},
                cluster_result_id: "default",
                cluster_method: "not_clustered",
            };
        }

        const targetClusterResultId =
            currentClusterResult?.cluster_result_id ||
            sliceInfo.cluster_result_id ||
            "default";

        // 用当前切片 ID 获取 plot-data 及相关统计；任一项失败也不影响其他（如 spot-metrics 用于小提琴图）
        const [plotRes, ncountRes, metricsRes, logRes] = await Promise.all([
            fetch(
                `${baseApi}/plot-data?slice_id=${currentSlice}&cluster_result_id=${targetClusterResultId}`,
            ),
            fetch(
                `${baseApi}/ncount_by_cluster?slice_id=${currentSlice}&cluster_result_id=${targetClusterResultId}`,
            ),
            fetch(`${baseApi}/spot-metrics?slice_id=${currentSlice}`),
            fetch(`${baseApi}/cluster-log?slice_id=${currentSlice}&cluster_result_id=${targetClusterResultId}`),
        ]);

        const plotData = plotRes.ok && plotRes.headers.get("content-type")?.includes("json")
            ? await plotRes.json()
            : [];
        const ncountData = ncountRes.ok && ncountRes.headers.get("content-type")?.includes("json")
            ? await ncountRes.json()
            : [];
        const metricsData = metricsRes.ok && metricsRes.headers.get("content-type")?.includes("json")
            ? await metricsRes.json()
            : [];
        const logData = logRes.ok && logRes.headers.get("content-type")?.includes("json")
            ? await logRes.json()
            : [];

        if (
            !comparisonModeActive &&
            sliceInfo.cluster_method !== "not_clustered"
        ) {
            umapData = await fecthUmapData(
                currentSlice,
                currentClusterResultId,
            );
        } else {
            umapData = null;
        }

        return {
            image,
            imageUrl: newImageUrl,  // 返回新的 imageUrl，让调用者统一设置
            plotData,
            sliceInfo,
            ncountData,
            metricsData,
            logData,
        };
    }

    async function drawExpressionMatrix() {
        const res = await fetch(`${baseApi}/expression-matrix`);
        const matrixData = await res.json(); // [{gene1: val1, gene2: val2, ...}, ...]

        const geneList = Object.keys(matrixData[0]);
        const barcodes = matrixData.map((_, i) => `spot ${i}`);
        const values = matrixData.map((row) => geneList.map((g) => row[g]));

        const trace = {
            z: values,
            x: geneList,
            y: barcodes,
            type: "heatmap",
            colorscale: "YlGnBu",
        };

        Plotly.newPlot("heatmapDiv", [trace], {
            title: "Gene Expression Heatmap (All Spots)",
            height: 800,
            xaxis: { title: "Gene" },
            yaxis: { title: "Spot (Barcode)", automargin: true },
        });
    }

    function handleSpotClick(detail) {
        if (!detail) return;

        const isLassoSelection =
            detail.lassoSelected || Array.isArray(detail.info);

        if (isLassoSelection) {
            lassoSelected = true;
            clickedInfo = Array.isArray(detail.info)
                ? [...detail.info]
                : detail.info;
            previewUrl = detail.previewUrl ?? "";
            lockedBarcode = null;
            hoveredBarcode = {
                barcode: -1,
                from: "spotPlot",
                persistent: false,
            };
            return;
        }

        if (clickedInfo && !Array.isArray(clickedInfo)) {
            clickedInfo.expression = null;
        }

        clickedInfo = detail.info ?? null;
        lassoSelected = false;
        previewUrl = detail.previewUrl ?? "";

        if (detail.info?.barcode) {
            lockedBarcode = detail.info.barcode;
            hoveredBarcode = {
                barcode: detail.info.barcode,
                from: "spotPlot",
                persistent: detail.info.barcode !== -1,
            };
        } else {
            lockedBarcode = null;
            hoveredBarcode = {
                barcode: -1,
                from: "spotPlot",
                persistent: false,
            };
        }
    }

    function handleHoverEvent(detail) {
        if (!detail) return;

        if (detail.persistent) {
            const resolvedBarcode =
                detail.barcode !== undefined && detail.barcode !== -1
                    ? detail.barcode
                    : null;
            lockedBarcode = resolvedBarcode;
            hoveredBarcode = {
                ...detail,
                barcode: resolvedBarcode ?? -1,
                persistent: resolvedBarcode !== null,
            };
            return;
        }

        if (detail.from === "spotPlot") {
            if (detail.barcode === -1) {
                if (lockedBarcode) {
                    hoveredBarcode = {
                        barcode: lockedBarcode,
                        from: "spotPlot",
                        persistent: true,
                    };
                } else {
                    hoveredBarcode = { ...detail, persistent: false };
                }
                return;
            }

            hoveredBarcode = {
                ...detail,
                persistent: detail.persistent ?? false,
            };
            return;
        }

        if (detail.from === "umap") {
            if (detail.barcode === -1) {
                if (lockedBarcode) {
                    hoveredBarcode = {
                        barcode: lockedBarcode,
                        from: "spotPlot",
                        persistent: true,
                    };
                } else {
                    hoveredBarcode = {
                        barcode: -1,
                        from: "spotPlot",
                        persistent: false,
                    };
                }
                return;
            }

            hoveredBarcode = {
                ...detail,
                persistent: detail.persistent ?? false,
            };
            return;
        }

        hoveredBarcode = { ...detail, persistent: detail.persistent ?? false };
    }

    function handleLassoClear() {
        // Restore original spatialData if we have preview updates
        if (hasPreviewUpdates && originalSpatialData) {
            spatialData = JSON.parse(JSON.stringify(originalSpatialData));
            console.log("🔄 Restored original spatialData from preview updates");
        }
        // Clear all preview updates
        previewUpdates = new Map();
        originalSpatialData = null;
        hasPreviewUpdates = false;
        
        lassoSelected = false;
        clickedInfo = null;
        previewUrl = "";
        lassoHover = null;
        lockedBarcode = null;
        lassoClearSignal += 1;
        hoveredBarcode = {
            barcode: -1,
            from: "spotPlot",
            persistent: false,
        };
    }

    async function handleClusterClick(detail) {
        const cluster = detail?.cluster;
        if (!cluster) return;

        selectedClusterForGenes = cluster;
        isLoadingClusterGenes = true;
        clusterTopGenes = null;

        try {
            const url = `${baseApi}/expression-by-cluster?cluster=${cluster}&slice_id=${currentSlice}&cluster_result_id=${currentClusterResultId}`;
            const response = await fetch(url);
            if (response.ok) {
                const data = await response.json();
                // Convert to array of {gene, expression} and sort by expression
                const genes = Object.entries(data)
                    .map(([gene, expr]) => ({ gene, expression: expr }))
                    .sort((a, b) => b.expression - a.expression)
                    .slice(0, 20); // Top 20 genes
                clusterTopGenes = genes;
            } else {
                console.error("Failed to fetch cluster genes:", response.statusText);
                clusterTopGenes = [];
            }
        } catch (error) {
            console.error("Error fetching cluster genes:", error);
            clusterTopGenes = [];
        } finally {
            isLoadingClusterGenes = false;
        }
    }

    // Track preview updates for real-time display
    let previewUpdates = new Map();
    // Save original spatialData before any preview updates
    let originalSpatialData = null;
    let hasPreviewUpdates = false;
    
    function saveOriginalSpatialData() {
        // Save original spatialData before any preview updates
        if (spatialData && Array.isArray(spatialData) && !hasPreviewUpdates) {
            originalSpatialData = JSON.parse(JSON.stringify(spatialData));
            hasPreviewUpdates = true;
            console.log("💾 Saved original spatialData for potential restore");
        }
    }
    
    function handlePreviewRecluster(info) {
        // Save original spatialData on first preview update
        saveOriginalSpatialData();
        // Store preview update without submitting to backend
        previewUpdates.set(info.barcode, {
            oldCluster: info.oldCluster,
            newCluster: info.newCluster,
        });
        previewUpdates = previewUpdates; // Trigger reactivity
        
        // Update spatialData display in real-time
        // Need to move barcode from old cluster trace to new cluster trace
        if (spatialData && Array.isArray(spatialData)) {
            const normalizedBarcode = `${info.barcode}`.trim();
            const oldClusterName = `${info.oldCluster}`.trim();
            const newClusterName = `${info.newCluster}`.trim();
            
            // Skip if old and new cluster are the same
            if (oldClusterName === newClusterName) {
                return;
            }
            
            // First pass: find old and new trace indices, and extract barcode data
            let oldTraceIndex = -1;
            let newTraceIndex = -1;
            let barcodeIndexInOldTrace = -1;
            let barcodeX = null;
            let barcodeY = null;
            
            for (let i = 0; i < spatialData.length; i++) {
                const trace = spatialData[i];
                if (!trace) continue;
                
                const traceName = `${trace.name}`.trim();
                const barcodes = trace.customdata ?? trace.text ?? trace.hovertext ?? trace.barcode ?? [];
                const normalizedBarcodes = Array.isArray(barcodes) ? barcodes : typeof barcodes === "string" ? [barcodes] : [];
                
                // Check if this is the old cluster trace and find barcode
                // Only find the barcode if we haven't found it yet (to ensure we only update one spot)
                if (traceName === oldClusterName && oldTraceIndex === -1) {
                    for (let j = 0; j < normalizedBarcodes.length; j++) {
                        if (`${normalizedBarcodes[j]}`.trim() === normalizedBarcode) {
                            oldTraceIndex = i;
                            barcodeIndexInOldTrace = j;
                            // Extract x, y coordinates
                            if (trace.x && Array.isArray(trace.x) && j < trace.x.length) {
                                barcodeX = trace.x[j];
                            }
                            if (trace.y && Array.isArray(trace.y) && j < trace.y.length) {
                                barcodeY = trace.y[j];
                            }
                            break; // Only update the first matching barcode
                        }
                    }
                }
                
                // Check if this is the new cluster trace
                if (traceName === newClusterName) {
                    newTraceIndex = i;
                }
            }
            
            // If we found the barcode in old trace, move it to new trace
            if (oldTraceIndex >= 0 && newTraceIndex >= 0 && barcodeIndexInOldTrace >= 0) {
                // Create new spatialData array with barcode moved
                spatialData = spatialData.map((trace, traceIdx) => {
                    if (!trace) return trace;
                    
                    const barcodes = trace.customdata ?? trace.text ?? trace.hovertext ?? trace.barcode ?? [];
                    const normalizedBarcodes = Array.isArray(barcodes) ? barcodes : typeof barcodes === "string" ? [barcodes] : [];
                    
                    // Remove barcode from old trace
                    if (traceIdx === oldTraceIndex) {
                        const updatedTrace = { ...trace };
                        const newBarcodes = [...normalizedBarcodes];
                        newBarcodes.splice(barcodeIndexInOldTrace, 1);
                        
                        // Update barcode array
                        if (trace.customdata) {
                            updatedTrace.customdata = newBarcodes;
                        } else if (trace.text) {
                            updatedTrace.text = newBarcodes;
                        } else if (trace.hovertext) {
                            updatedTrace.hovertext = newBarcodes;
                        } else if (trace.barcode) {
                            updatedTrace.barcode = newBarcodes;
                        }
                        
                        // Update x, y arrays
                        if (trace.x && Array.isArray(trace.x)) {
                            const newX = [...trace.x];
                            newX.splice(barcodeIndexInOldTrace, 1);
                            updatedTrace.x = newX;
                        }
                        if (trace.y && Array.isArray(trace.y)) {
                            const newY = [...trace.y];
                            newY.splice(barcodeIndexInOldTrace, 1);
                            updatedTrace.y = newY;
                        }
                        
                        return updatedTrace;
                    }
                    
                    // Add barcode to new trace
                    if (traceIdx === newTraceIndex) {
                        const updatedTrace = { ...trace };
                        const newBarcodes = [...normalizedBarcodes, normalizedBarcode];
                        
                        // Update barcode array
                        if (trace.customdata) {
                            updatedTrace.customdata = newBarcodes;
                        } else if (trace.text) {
                            updatedTrace.text = newBarcodes;
                        } else if (trace.hovertext) {
                            updatedTrace.hovertext = newBarcodes;
                        } else if (trace.barcode) {
                            updatedTrace.barcode = newBarcodes;
                        }
                        
                        // Add x, y coordinates
                        if (barcodeX !== null) {
                            const newX = trace.x ? [...trace.x] : [];
                            newX.push(barcodeX);
                            updatedTrace.x = newX;
                        }
                        if (barcodeY !== null) {
                            const newY = trace.y ? [...trace.y] : [];
                            newY.push(barcodeY);
                            updatedTrace.y = newY;
                        }
                        
                        return updatedTrace;
                    }
                    
                    return trace;
                });
                
                // Force reactivity by creating new array reference
                spatialData = JSON.parse(JSON.stringify(spatialData));
            }
        }
    }
    
    function handleBatchPreviewRecluster(info) {
        if (!info || !info.updates || !Array.isArray(info.updates)) return;
        
        // Save original spatialData on first preview update
        saveOriginalSpatialData();
        
        // Store all preview updates
        info.updates.forEach((update) => {
            previewUpdates.set(update.barcode, {
                oldCluster: update.oldCluster,
                newCluster: update.newCluster,
            });
        });
        previewUpdates = previewUpdates; // Trigger reactivity
        
        // Batch update spatialData
        if (spatialData && Array.isArray(spatialData)) {
            // Build a map of trace name to trace index
            const traceIndexByName = new Map();
            spatialData.forEach((trace, idx) => {
                if (trace && trace.name) {
                    traceIndexByName.set(`${trace.name}`.trim(), idx);
                }
            });
            
            // Build barcode location map: barcode -> {traceIdx, pointIdx, x, y}
            const barcodeLocationMap = new Map();
            spatialData.forEach((trace, traceIdx) => {
                if (!trace) return;
                const barcodes = trace.customdata ?? trace.text ?? trace.hovertext ?? trace.barcode ?? [];
                const normalizedBarcodes = Array.isArray(barcodes) ? barcodes : typeof barcodes === "string" ? [barcodes] : [];
                normalizedBarcodes.forEach((barcode, pointIdx) => {
                    const key = `${barcode}`.trim();
                    if (!barcodeLocationMap.has(key)) {
                        barcodeLocationMap.set(key, {
                            traceIdx,
                            pointIdx,
                            x: trace.x && Array.isArray(trace.x) && pointIdx < trace.x.length ? trace.x[pointIdx] : null,
                            y: trace.y && Array.isArray(trace.y) && pointIdx < trace.y.length ? trace.y[pointIdx] : null,
                        });
                    }
                });
            });
            
            // Collect all removals and additions
            const removals = []; // [{traceIdx, pointIdx}]
            const additions = []; // [{traceIdx, barcode, x, y}]
            
            info.updates.forEach((update) => {
                const normalizedBarcode = `${update.barcode}`.trim();
                const oldClusterName = `${update.oldCluster}`.trim();
                const newClusterName = `${update.newCluster}`.trim();
                
                if (oldClusterName === newClusterName) return;
                
                const location = barcodeLocationMap.get(normalizedBarcode);
                const newTraceIdx = traceIndexByName.get(newClusterName);
                
                if (location && newTraceIdx !== undefined) {
                    removals.push({
                        traceIdx: location.traceIdx,
                        pointIdx: location.pointIdx,
                        barcode: normalizedBarcode,
                    });
                    additions.push({
                        traceIdx: newTraceIdx,
                        barcode: normalizedBarcode,
                        x: location.x,
                        y: location.y,
                    });
                }
            });
            
            // Sort removals by pointIdx descending so we can safely remove from end to start
            removals.sort((a, b) => {
                if (a.traceIdx !== b.traceIdx) return a.traceIdx - b.traceIdx;
                return b.pointIdx - a.pointIdx;
            });
            
            // Apply all changes to create new spatialData
            const newSpatialData = spatialData.map((trace, traceIdx) => {
                if (!trace) return trace;
                
                const traceRemovals = removals.filter(r => r.traceIdx === traceIdx);
                const traceAdditions = additions.filter(a => a.traceIdx === traceIdx);
                
                if (traceRemovals.length === 0 && traceAdditions.length === 0) {
                    return trace;
                }
                
                const updatedTrace = { ...trace };
                let barcodes = [...(trace.customdata ?? trace.text ?? trace.hovertext ?? trace.barcode ?? [])];
                let xArr = trace.x ? [...trace.x] : [];
                let yArr = trace.y ? [...trace.y] : [];
                
                // Remove barcodes (already sorted by pointIdx descending)
                traceRemovals.forEach((removal) => {
                    barcodes.splice(removal.pointIdx, 1);
                    if (xArr.length > removal.pointIdx) xArr.splice(removal.pointIdx, 1);
                    if (yArr.length > removal.pointIdx) yArr.splice(removal.pointIdx, 1);
                });
                
                // Add new barcodes
                traceAdditions.forEach((addition) => {
                    barcodes.push(addition.barcode);
                    if (addition.x !== null) xArr.push(addition.x);
                    if (addition.y !== null) yArr.push(addition.y);
                });
                
                // Update the trace arrays
                if (trace.customdata) updatedTrace.customdata = barcodes;
                else if (trace.text) updatedTrace.text = barcodes;
                else if (trace.hovertext) updatedTrace.hovertext = barcodes;
                else if (trace.barcode) updatedTrace.barcode = barcodes;
                
                updatedTrace.x = xArr;
                updatedTrace.y = yArr;
                
                return updatedTrace;
            });
            
            // Force reactivity by creating new array reference
            spatialData = JSON.parse(JSON.stringify(newSpatialData));
        }
    }
    
    async function handleClusterUpdate(info) {
        console.log(
            info.barcode,
            info.newCluster,
            info.oldCluster,
            info.comment,
        );
        const res = await fetch(`${baseApi}/update-cluster`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                slice_id: currentSlice,
                cluster_result_id: currentClusterResultId,
                barcode: info.barcode,
                old_cluster: info.oldCluster,
                new_cluster: info.newCluster,
                comment: info.comment,
            }),
        });

        if (res.ok) {
            // ✅ 成功更新后重新获取并刷新 spatialData
            const [updatedPlotRes, updatedLogRes] = await Promise.all([
                fetch(`${baseApi}/plot-data?slice_id=${currentSlice}&cluster_result_id=${currentClusterResultId}`),
                fetch(`${baseApi}/cluster-log?slice_id=${currentSlice}&cluster_result_id=${currentClusterResultId}`),
            ]);

            // ✅ 更新当前点击的点的聚类
            if (clickedInfo && typeof clickedInfo === 'object' && !Array.isArray(clickedInfo)) {
                clickedInfo.cluster = info.newCluster;
            } else if (Array.isArray(clickedInfo)) {
                // Update the specific entry in the array
                clickedInfo = clickedInfo.map(item => {
                    if (typeof item === 'object' && item && (item.barcode === info.barcode || item === info.barcode)) {
                        return { ...item, cluster: info.newCluster };
                    }
                    return item;
                });
            }

            const newData = await updatedPlotRes.json();
            // 强制更新spatialData，触发plot重新绘制
            spatialData = JSON.parse(JSON.stringify(newData)); // 强制换引用
            allLog = await updatedLogRes.json();
            
            // Clear preview update for this barcode
            previewUpdates.delete(info.barcode);
            previewUpdates = previewUpdates;
            
            console.log("✅ Plot data updated after cluster change:", {
                barcode: info.barcode,
                oldCluster: info.oldCluster,
                newCluster: info.newCluster,
                spatialDataLength: spatialData.length,
            });
        }
    }
    
    function handleCompleteRecluster() {
        // Clear all preview updates
        previewUpdates = new Map();
        // Clear saved original data since changes are now committed
        originalSpatialData = null;
        hasPreviewUpdates = false;
        // Exit lasso mode after completing
        lassoSelected = false;
        clickedInfo = null;
        previewUrl = "";
        lassoHover = null;
        lockedBarcode = null;
        hoveredBarcode = {
            barcode: -1,
            from: "spotPlot",
            persistent: false,
        };
    }

    async function recluster() {
        reclusering = true;
        reclustered = false;
        const res = await fetch(`${baseApi}/recluster`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                slice_id: currentSlice,
                barcode: clickedInfo,
                method: currentClusterResult?.method || currentMethod,
                cluster_result_id: currentClusterResultId,
            }),
        });

        if (res.ok) {
            const data = await res.json();
            console.log("返回的数据内容：", data);
            clickedInfo = data;
            // lassoSelected = false;
            reclustered = true;
            // dispatch("spotClick", {
            //     info: data,
            //     lassoSelected: true,
            // });
            reclusering = false;
        }
    }

    async function refreshSpatialState() {
        if (isLoadingClusterResult) return;
        isInitializing = true;
        // 如果 loading 还没有设置，则设置它（可能已经在外部设置了）
        if (!loading) {
            loading = true;
            await tick(); // 确保 loading 状态先更新
        }
        // 现在清空数据，此时 loading 已经是 true，条件判断不会触发额外的加载状态
        spatialData = [];
        imageUrl = "";
        spatialInfo = null;
        spotMetricsData = null;

        try {
            const {
                image: loadedImage,
                imageUrl: newImageUrl,
                ncountData,
                plotData,
                sliceInfo,
                metricsData,
                logData,
            } = await fetchSpatial();

            const plotList = Array.isArray(plotData) ? plotData : [];
            const fallbackClusters = Array.from(
                new Set(plotList.map((trace) => `${trace.name}`)),
            ).sort((a, b) => {
                const numA = parseFloat(a);
                const numB = parseFloat(b);
                if (Number.isNaN(numA) || Number.isNaN(numB)) {
                    return `${a}`.localeCompare(`${b}`);
                }
                return numA - numB;
            });

            const targetClusterResultId =
                currentClusterResult?.cluster_result_id
                    ? currentClusterResult.cluster_result_id
                    : sliceInfo.cluster_result_id || "default";

            currentClusterResultId = targetClusterResultId;
            if (
                !currentClusterResult ||
                !currentClusterResult.cluster_result_id
            ) {
                currentClusterResult = {
                    cluster_result_id: currentClusterResultId,
                    method: sliceInfo.cluster_method,
                    n_clusters: sliceInfo.n_clusters,
                    epoch: sliceInfo.epoch,
                };
            }

            const mappingLoaded = await fetchClusterColorMapping(
                currentSlice,
                currentClusterResultId,
                fallbackClusters,
            );
            if (!mappingLoaded) {
                buildClusterColorScale(fallbackClusters);
            }

            // 批量更新状态，避免多次触发重新渲染
            image = loadedImage;
            imageUrl = newImageUrl;  // 使用返回的 imageUrl
            spatialData = plotList;
            spatialData.sort((a, b) => {
                const numA = parseFloat(a.name);
                const numB = parseFloat(b.name);
                return numA - numB;
            });
            console.log("spatialData", spatialData);
            // 始终保留 sliceInfo（含空 info_details），便于页面显示 label、value 可空
            spatialInfo = sliceInfo ?? null;
            currentMethod = sliceInfo.cluster_method || currentMethod;
            if (!prevSlice && sliceInfo?.cluster_method) {
                uiClusteringMethod = sliceInfo.cluster_method;
            }
            epoch = sliceInfo.epoch ?? 500;
            n_clusters = sliceInfo.n_clusters ?? 7;
            ncountSpatialData = Array.isArray(ncountData) ? ncountData : [];
            spotMetricsData = Array.isArray(metricsData) ? metricsData : [];

            allLog = logData;
            clusterGeneExpression = null;
            clusterGeneDot = null;
            cellChat = null;
        } finally {
            loading = false;
            isInitializing = false;
        }
    }

    function updateClusterMeta(plotData, clustersInfo = null) {
        const clusterSet = new Set();
        plotData.forEach((trace) => clusterSet.add(`${trace.name}`));
        availableClusters = Array.from(clusterSet).sort((a, b) => {
            const numA = parseFloat(a);
            const numB = parseFloat(b);
            if (Number.isNaN(numA) || Number.isNaN(numB)) {
                return `${a}`.localeCompare(`${b}`);
            }
            return numA - numB;
        });

        if (
            clustersInfo &&
            Array.isArray(clustersInfo) &&
            clustersInfo.length
        ) {
            const normalized = clustersInfo.map(
                (item) => `${item.name ?? item.cluster ?? item}`,
            );
            availableClusters = normalized;
        }

        if (!clusterColorScale || typeof clusterColorScale !== "function") {
            setDefaultClusterColorScale(availableClusters);
        }
    }

    async function iniCluster(params = null) {
        const method = params?.method || uiClusteringMethod;
        const clusters = params?.n_clusters || n_clusters;
        const epochs = params?.epoch || epoch;

        loading = true;
        isLoadingClusterResult = true;
        spatialData = [];
        // 不清空 image/imageUrl，否则空间预览底图会消失，只更新 overlay (spatialData)
        ncountSpatialData = null;
        spotMetricsData = null;
        allLog = null;
        cellChat = null;
        hvg = {};
        umapData = null;
        currentClusterResultId = "default";
        currentClusterResult = null;
        buildClusterColorScale();

        try {
            const clusterRes = await fetch(baseApi + "/run-clustering", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    n_clusters: clusters,
                    method: method,
                    slice_id: currentSlice,
                    epoch: epochs,
                }),
            });

            if (!clusterRes.ok) {
                throw new Error(`Run clustering failed (${clusterRes.status})`);
            }

            const runResult = await clusterRes.json();
            currentMethod = method;
            n_clusters = clusters;
            epoch = epochs;

            // 计算新创建的 cluster_result_id（与后端逻辑一致）
            const newClusterResultId = `${currentSlice}_${method}_${clusters}_${epochs}`;
            currentClusterResultId = newClusterResultId;
            currentClusterResult = {
                cluster_result_id: newClusterResultId,
                method: method,
                n_clusters: clusters,
                epoch: epochs,
            };

            if (Array.isArray(runResult) && runResult.length) {
                const fallbackClusters = Array.from(
                    new Set(runResult.map((trace) => `${trace.name}`)),
                ).sort((a, b) => {
                    const numA = parseFloat(a);
                    const numB = parseFloat(b);
                    if (Number.isNaN(numA) || Number.isNaN(numB)) {
                        return `${a}`.localeCompare(`${b}`);
                    }
                    return numA - numB;
                });
                buildClusterColorScale(fallbackClusters);
                spatialData = runResult;
            }

            // 统计数据是切片级别的，不会因为聚类结果改变而改变
            // 如果统计数据还没有加载，则加载（通常在 fetchSpatial 中已加载）
            if (!ncountSpatialData || !spotMetricsData) {
                try {
                    const [ncountRes, metricsRes] = await Promise.all([
                        fetch(
                            `${baseApi}/ncount_by_cluster?slice_id=${currentSlice}&cluster_result_id=default`,
                        ),
                        fetch(
                            `${baseApi}/spot-metrics?slice_id=${currentSlice}`,
                        ),
                    ]);

                    if (ncountRes.ok) {
                        ncountSpatialData = await ncountRes.json();
                    }
                    if (metricsRes.ok) {
                        spotMetricsData = await metricsRes.json();
                    }
                } catch (error) {
                    console.warn("Failed to load statistics data:", error);
                }
            }
        } catch (error) {
            console.error("Failed to run clustering:", error);
        } finally {
            loading = false;
            isLoadingClusterResult = false;
            
            // Preload all clusters' marker genes after clustering
            if (currentClusterResultId) {
                try {
                    const markerGenesRes = await fetch(
                        `${baseApi}/expression-by-all-clusters?slice_id=${currentSlice}&cluster_result_id=${currentClusterResultId}`
                    );
                    if (markerGenesRes.ok) {
                        allClustersMarkerGenes = await markerGenesRes.json();
                        console.log("✅ Preloaded marker genes for all clusters after clustering:", Object.keys(allClustersMarkerGenes).length, "clusters");
                    }
                } catch (err) {
                    console.warn("Failed to preload marker genes after clustering:", err);
                    allClustersMarkerGenes = {};
                }
                
                // Set cellTypeAnnotations to empty object (no external cell type annotation)
                cellTypeAnnotations = {};
            }
        }

        // 在 finally 之后刷新聚类结果列表，避免影响 loading 状态
        if (clusterManagementRef?.refreshResults) {
            clusterManagementRef.refreshResults({ autoSelectFirst: false });
        }
    }

    async function loadClusterResult(
        clusterResultId,
        method,
        n_clusters_val,
        epoch_val,
    ) {
        // Clear any existing timeout
        if (loadingTimeoutId) {
            clearTimeout(loadingTimeoutId);
            loadingTimeoutId = null;
        }
        
        isLoadingClusterResult = true;
        // Reset cell type annotations when loading a new cluster result
        cellTypeAnnotations = null;
        
        // Set up a fallback timeout to cancel loading if plotRendered event doesn't fire
        const setupFallbackTimeout = () => {
            if (loadingTimeoutId) {
                clearTimeout(loadingTimeoutId);
            }
            loadingTimeoutId = setTimeout(() => {
                if (isLoadingClusterResult) {
                    console.log("⏱️ Canceling loading (fallback timeout - plotRendered event may not have fired)");
                    isLoadingClusterResult = false;
                }
                loadingTimeoutId = null;
            }, 2000);
        };
        
        try {
            // Check if there's an aligned cluster_result_id for this source
            // If the current result is already aligned (result_name starts with "Aligned from"), use it directly
            // Otherwise, look for an aligned version of this source
            let targetClusterResultId = clusterResultId || "default";
            if (clusterResultsList && clusterResultsList.length > 0) {
                const sourceId = clusterResultId || "default";
                
                // First, check if the current result is already an aligned result
                const currentResult = clusterResultsList.find(
                    (r) => r.cluster_result_id === sourceId
                );
                
                if (currentResult && currentResult.result_name && 
                    currentResult.result_name.startsWith("Aligned from")) {
                    // Current result is already aligned, use it directly
                    targetClusterResultId = sourceId;
                } else {
                    // Look for aligned result: result_name format is "Aligned from {source_cluster_result_id}"
                    // Match exactly "Aligned from {sourceId}" (with optional space)
                    const alignedResult = clusterResultsList.find(
                        (result) => {
                            if (!result.result_name || !result.result_name.startsWith("Aligned from")) {
                                return false;
                            }
                            // Extract the source ID from "Aligned from {sourceId}"
                            const match = result.result_name.match(/^Aligned from\s+(.+)$/);
                            if (match && match[1]) {
                                const alignedSourceId = match[1].trim();
                                return alignedSourceId === sourceId;
                            }
                            return false;
                        }
                    );
                    if (alignedResult && alignedResult.cluster_result_id) {
                        targetClusterResultId = alignedResult.cluster_result_id;
                    }
                }
            }
            
            currentClusterResultId = targetClusterResultId;
            // 统计数据是切片级别的，不需要重新加载
            const [plotRes, logRes] = await Promise.all([
                fetch(
                    `${baseApi}/plot-data?slice_id=${currentSlice}&cluster_result_id=${targetClusterResultId}`,
                ),
                fetch(`${baseApi}/cluster-log?slice_id=${currentSlice}&cluster_result_id=${targetClusterResultId}`),
            ]);

            const plotData = await plotRes.json();
            const logData = await logRes.json();

            const fallbackClusters = Array.from(
                new Set((plotData || []).map((trace) => `${trace.name}`)),
            ).sort((a, b) => {
                const numA = parseFloat(a);
                const numB = parseFloat(b);
                if (Number.isNaN(numA) || Number.isNaN(numB)) {
                    return `${a}`.localeCompare(`${b}`);
                }
                return numA - numB;
            });
            
            console.log("🎨 loadClusterResult: Extracted clusters from plotData", {
                clusterResultId: currentClusterResultId,
                fallbackClusters,
                fallbackClustersLength: fallbackClusters.length,
                plotDataTracesCount: plotData.length
            });
            
            // First, try to fetch color mapping from server (this may return cached result)
            const colorMappingSuccess = await fetchClusterColorMapping(
                currentSlice,
                currentClusterResultId,
                fallbackClusters,
            );
            
            // Always rebuild color scale with the actual clusters from plotData
            // This ensures the color scale domain matches the actual data, even if fetchClusterColorMapping
            // returned a cached result with fewer clusters. We need to ensure all clusters in plotData
            // are included in the color scale domain.
            // Use lastColorMapping to preserve color mappings from fetchClusterColorMapping
            buildClusterColorScale(fallbackClusters, lastColorMapping);
            
            console.log("🎨 loadClusterResult: Color scale updated", {
                clusterResultId: currentClusterResultId,
                colorScaleVersion: clusterColorScaleVersion,
                hasColorScale: typeof clusterColorScale === 'function',
                domainLength: clusterColorScale && clusterColorScale.domain ? clusterColorScale.domain().length : 0,
                domain: clusterColorScale && clusterColorScale.domain ? clusterColorScale.domain().slice(0, 10) : [],
                fallbackClustersLength: fallbackClusters.length
            });
            
            // Don't cancel loading here - wait for plot to render with colors
            // The loading will be canceled when plotRendered event is fired
            // But set up a fallback timeout in case plotRendered doesn't fire
            setupFallbackTimeout();
            
            updateClusterMeta(plotData);

            imageUrl = `${baseApi}/images/${currentSlice}/tissue_hires_image.png`;
            if (!imageCache.has(imageUrl)) {
                image = await loadImage(imageUrl);
            } else {
                image = imageCache.get(imageUrl);
            }

            spatialData = plotData;

            // 统计数据是切片级别的，保持不变，不需要更新
            allLog = logData;

            if (method) {
                currentMethod = method;
            }
            if (n_clusters_val != null) n_clusters = n_clusters_val;
            if (epoch_val != null) epoch = epoch_val;
            currentClusterResult = {
                cluster_result_id: currentClusterResultId,
                method: currentMethod,
                n_clusters,
                epoch,
            };

            try {
                umapData = await fecthUmapData(
                    currentSlice,
                    currentClusterResultId,
                );
            } catch (err) {
                console.warn("Failed to load UMAP data:", err);
                umapData = null;
            }
            
            // Preload all clusters' marker genes (only need to compute once)
            try {
                const markerGenesRes = await fetch(
                    `${baseApi}/expression-by-all-clusters?slice_id=${currentSlice}&cluster_result_id=${currentClusterResultId}`
                );
                if (markerGenesRes.ok) {
                    allClustersMarkerGenes = await markerGenesRes.json();
                    console.log("✅ Preloaded marker genes for all clusters:", Object.keys(allClustersMarkerGenes).length, "clusters");
                }
            } catch (err) {
                console.warn("Failed to preload marker genes:", err);
                allClustersMarkerGenes = {};
            }
            
            // Set cellTypeAnnotations to empty object (no external cell type annotation)
            cellTypeAnnotations = {};
        } catch (error) {
            console.error("Error loading cluster result:", error);
            // On error, cancel loading immediately
            if (loadingTimeoutId) {
                clearTimeout(loadingTimeoutId);
                loadingTimeoutId = null;
            }
            await tick();
            isLoadingClusterResult = false;
        }
    }

    $: clusterDistribution = Array.isArray(spatialData)
        ? spatialData
              .map((trace) => {
                  if (!trace) return null;
                  const clusterName = `${trace.name ?? "unknown"}`;
                  const counts = [
                      Array.isArray(trace.x) ? trace.x.length : 0,
                      Array.isArray(trace.y) ? trace.y.length : 0,
                      Array.isArray(trace.customdata)
                          ? trace.customdata.length
                          : 0,
                      Array.isArray(trace.text) ? trace.text.length : 0,
                      trace.marker && Array.isArray(trace.marker?.size)
                          ? trace.marker.size.length
                          : 0,
                  ];
                  const count = Math.max(...counts, 0);
                  return count > 0 ? { cluster: clusterName, count } : null;
              })
              .filter((item) => item !== null)
        : [];

    let clusterManagementRef;
    let clusterResultsScroller;

    // Currently selected cluster result full meta (including timestamps, metrics, etc.)
    $: selectedClusterResultMeta =
        clusterResultsList?.find(
            (r) => r.cluster_result_id === currentClusterResultId,
        ) ?? null;

    async function handleRunCluster(event) {
        await iniCluster(event.detail);
        comparisonModeActive = true;
        umapData = null;
        // Clear selection when entering comparison mode
        currentClusterResultId = "default";
        currentClusterResult = null;
        await tick();
        // refreshResults 已经在 iniCluster 中调用了，这里不需要重复调用
    }

    async function enterComparisonMode() {
        comparisonModeActive = true;
        currentModule = "clustering";
        umapData = null;
        // Clear selection when entering comparison mode
        currentClusterResultId = "default";
        currentClusterResult = null;
        await tick();
        if (clusterManagementRef?.clearSelection) {
            clusterManagementRef.clearSelection();
        }
    }

    async function enterDownstreamModule() {
        // Ensure we're not in comparison mode
        if (comparisonModeActive) {
            comparisonModeActive = false;
        }
        // Ensure we have a valid cluster result selected
        if (!currentClusterResultId || currentClusterResultId === "default") {
            console.warn("Cannot enter downstream module: no cluster result selected");
            return;
        }
        
        // Switch to downstream module first
        currentModule = "downstream";
        
        // Check if data is already loaded for this cluster result
        // Only load if data is missing or incomplete
        const needsLoading = !spatialData || 
                           spatialData.length === 0 || 
                           !image ||
                           !clusterColorScale ||
                           typeof clusterColorScale !== 'function';
        
        if (needsLoading && currentClusterResultId && currentClusterResultId !== "default") {
            // loadClusterResult will handle the loading state internally
            // Don't set isLoadingClusterResult here to avoid duplicate loading states
            const currentResult = clusterResultsList?.find(
                (r) => r.cluster_result_id === currentClusterResultId
            );
            if (currentResult) {
                await loadClusterResult(
                    currentClusterResultId,
                    currentResult.method,
                    currentResult.n_clusters,
                    currentResult.epoch,
                );
            } else {
                // If result not found in list, still try to load it
                await loadClusterResult(currentClusterResultId);
            }
        }
        // If data is already loaded, no need to show loading
    }

    $: {
        if (comparisonModeActive && clusterManagementRef?.getScrollContainer) {
            const container = clusterManagementRef.getScrollContainer();
            if (clusterResultsScroller !== container) {
                clusterResultsScroller = container;
            }
        }

        if (!comparisonModeActive) {
            clusterResultsScroller = null;
        }
    }

    async function handleSelectClusterResult(event) {
        const { result, autoSelected, isPreview = false, barcodes, cluster } = event.detail;
        
        // Handle deselection: if result is null, clear selection
        if (!result || !result.cluster_result_id) {
            currentClusterResultId = "default";
            currentClusterResult = null;
            return;
        }
        
        let targetId = result.cluster_result_id;

        // Check if there's an aligned version of this result
        if (targetId && clusterResultsList && clusterResultsList.length > 0) {
            const sourceId = targetId;
            
            // First, check if the current result is already an aligned result
            const currentResult = clusterResultsList.find(
                (r) => r.cluster_result_id === sourceId
            );
            
            if (currentResult && currentResult.result_name && 
                currentResult.result_name.startsWith("Aligned from")) {
                // Current result is already aligned, use it directly
                targetId = sourceId;
            } else {
                // Look for aligned result: result_name format is "Aligned from {source_cluster_result_id}"
                const alignedResult = clusterResultsList.find(
                    (r) => {
                        if (!r.result_name || !r.result_name.startsWith("Aligned from")) {
                            return false;
                        }
                        // Extract the source ID from "Aligned from {sourceId}"
                        const match = r.result_name.match(/^Aligned from\s+(.+)$/);
                        if (match && match[1]) {
                            const alignedSourceId = match[1].trim();
                            return alignedSourceId === sourceId;
                        }
                        return false;
                    }
                );
                if (alignedResult && alignedResult.cluster_result_id) {
                    targetId = alignedResult.cluster_result_id;
                }
            }
        }

        if (targetId) {
            currentClusterResultId = targetId;
            currentClusterResult = {
                cluster_result_id: targetId,
                method: result?.method,
                n_clusters: result?.n_clusters,
                epoch: result?.epoch,
            };
        }

        // For preview selection (just highlighting), only update the ID and return immediately
        if (isPreview) {
            // Highlight should be immediate, no data loading needed
            // If deselecting (targetId is null or "default"), that's already handled above
            return;
        }

        // Only leave comparison view and jump into detail when this is not a preview selection
            if (!autoSelected) {
                comparisonModeActive = false;
                // When user actively opens a result detail: focus on result, collapse slice info
                isSliceSectionOpen = false;
                isClusterSectionOpen = true;
                
                // If barcodes are provided (from Sankey double-click), enter lasso mode with those barcodes
                if (barcodes && barcodes.length > 0) {
                    lassoSelected = true;
                    // Store barcodes with cluster info for lassomode display
                    clickedInfo = barcodes.map((barcode) => ({
                        barcode,
                        clusterInfo: {
                            cluster: cluster || "",
                        },
                    }));
                } else if (lassoSelected) {
                    // Exit lasso mode when opening cluster result page from sidebar (no barcodes)
                    // Restore original spatialData if we have preview updates
                    if (hasPreviewUpdates && originalSpatialData) {
                        spatialData = JSON.parse(JSON.stringify(originalSpatialData));
                    }
                    // Clear all preview updates
                    previewUpdates = new Map();
                    originalSpatialData = null;
                    hasPreviewUpdates = false;
                    
                    lassoSelected = false;
                    clickedInfo = null;
                    previewUrl = "";
                    lassoHover = null;
                    lockedBarcode = null;
                    lassoClearSignal += 1;
                    hoveredBarcode = {
                        barcode: -1,
                        from: "spotPlot",
                        persistent: false,
                    };
                }
            // If we're in downstream mode, clicking a result in the left sidebar switches the analysis
            // The DownstreamAnalysis component will react to currentClusterResultId changes and update
        }

        const shouldSkip =
            autoSelected &&
            (isInitializing ||
                (targetId &&
                    targetId === currentClusterResultId &&
                    spatialData &&
                    spatialData.length > 0 &&
                    !loading));

        const fallbackClusters =
            spatialData && spatialData.length
                ? Array.from(
                      new Set(spatialData.map((trace) => `${trace.name}`)),
                  ).sort((a, b) => {
                      const numA = parseFloat(a);
                      const numB = parseFloat(b);
                      if (Number.isNaN(numA) || Number.isNaN(numB)) {
                          return `${a}`.localeCompare(`${b}`);
                      }
                      return numA - numB;
                  })
                : Array.isArray(result?.clusters)
                  ? result.clusters.map((cluster) => `${cluster}`)
                  : [];

        if (shouldSkip) {
            await fetchClusterColorMapping(
                currentSlice,
                currentClusterResultId,
                fallbackClusters,
            );
            // Even if skipping, ensure color scale is updated with all clusters
            buildClusterColorScale(fallbackClusters, lastColorMapping);
            return;
        }

        // Always call loadClusterResult to ensure data and color scale are properly updated
        // This is especially important in downstream analysis mode
        await loadClusterResult(
            targetId,
            result?.method,
            result?.n_clusters,
            result?.epoch,
        );
    }

    async function handleFlowSegmentClick(detail) {
        if (!detail) return;
        
        const { clusterResultId, barcodes, fromCluster, toCluster, result } = detail;
        if (!clusterResultId || !barcodes || !barcodes.length) return;
        
        // Close comparison mode
        comparisonModeActive = false;
        // Focus sidebar on result when entering lasso from a flow segment
        isSliceSectionOpen = false;
        isClusterSectionOpen = true;
        
        // Set lasso selection state with flow info
        lassoSelected = true;
        // Store barcodes with flow info for lassomode display
        clickedInfo = barcodes.map((barcode, index) => ({
            barcode,
            flowInfo: {
                fromCluster,
                toCluster,
            },
        }));
        
        // Switch to the target cluster result
        currentClusterResultId = clusterResultId;
        currentClusterResult = {
            cluster_result_id: clusterResultId,
            method: result?.method,
            n_clusters: result?.n_clusters,
            epoch: result?.epoch,
        };
        
        // Load the cluster result
        await loadClusterResult(
            clusterResultId,
            result?.method,
            result?.n_clusters,
            result?.epoch,
        );
    }

    function initSplit() {
        if (
            splitContainer &&
            topPanel &&
            bottomPanel &&
            !splitInstance &&
            !comparisonModeActive
        ) {
            splitInstance = Split([topPanel, bottomPanel], {
                direction: "vertical",
                sizes: [60, 40], // Initial sizes: 60% top, 40% bottom
                minSize: [200, 200], // Minimum sizes for each panel
                gutterSize: 8,
                cursor: "row-resize",
                onDrag: () => {
                    // Trigger resize for Plotly charts when dragging
                    if (typeof window !== "undefined" && window.Plotly) {
                        window.dispatchEvent(new Event("resize"));
                    }
                },
            });
        }
    }

    onMount(async () => {
        await refreshSpatialState();

        // Initialize split.js after DOM is ready
        tick().then(() => {
            initSplit();
        });
    });

    // Ensure cluster results are loaded once when *entering* comparison mode with empty list (not every time list stays empty)
    $: {
        const justEnteredComparison = comparisonModeActive && !prevComparisonModeActive;
        prevComparisonModeActive = comparisonModeActive;
        if (
            justEnteredComparison &&
            clusterManagementRef &&
            typeof clusterManagementRef.refreshResults === "function" &&
            (!clusterResultsList || clusterResultsList.length === 0) &&
            !isClusterResultsLoading
        ) {
            clusterManagementRef.refreshResults({ autoSelectFirst: false });
        }
    }

    // Clean up and re-initialize split when comparisonModeActive changes
    $: if (comparisonModeActive && splitInstance) {
        // Clean up when entering comparison mode
        splitInstance.destroy();
        splitInstance = null;
    } else if (
        !comparisonModeActive &&
        splitContainer &&
        topPanel &&
        bottomPanel &&
        !splitInstance
    ) {
        // Initialize when exiting comparison mode
        tick().then(() => {
            initSplit();
        });
    }

    // Clean up split when switching away from clustering module
    // and re-initialize when switching back
    $: if (currentModule !== "clustering" && splitInstance) {
        // Clean up when leaving clustering module
        splitInstance.destroy();
        splitInstance = null;
    } else if (
        currentModule === "clustering" &&
        !comparisonModeActive &&
        splitContainer &&
        topPanel &&
        bottomPanel &&
        !splitInstance
    ) {
        // Initialize when switching back to clustering module
        tick().then(() => {
            initSplit();
        });
    }

    onDestroy(() => {
        // Clean up split instance
        if (splitInstance) {
            splitInstance.destroy();
            splitInstance = null;
        }
    });

    $: if (currentSlice !== prevSlice) {
        (async () => {
            if (prevSlice !== undefined) {
                loading = true;
                await tick();
            }

            try {
                if (prevSlice) {
                    const changeRes = await fetch(
                        baseApi + `/changeSlice?sliceid=${currentSlice}`,
                    );
                    if (!changeRes.ok) {
                        const errBody = await changeRes.json().catch(() => ({}));
                        const msg = errBody.detail || changeRes.statusText || "切换切片失败";
                        console.warn("切换切片失败:", currentSlice, msg);
                        currentSlice = prevSlice;
                        prevSlice = currentSlice;
                        loading = false;
                        alert(msg);
                        return;
                    }
                }

                if (prevSlice !== undefined) {
                    await refreshSpatialState();
                }
            } catch (err) {
                console.warn("切换切片加载失败:", currentSlice, err);
                currentSlice = prevSlice ?? currentSlice;
                loading = false;
            } finally {
                prevSlice = currentSlice;
            }
        })();
    }
</script>

    {#if
        loading ||
        isLoadingClusterResult ||
        isClusterResultsLoading ||
        isComparisonLoading ||
        (!spatialData || (Array.isArray(spatialData) && spatialData.length === 0))}
    <div
        class="fixed inset-0 z-50 flex justify-center items-center bg-white/80"
    >
        <ProgressRing
            value={null}
            size="size-14"
            meterStroke="stroke-slate-300"
            trackStroke="stroke-slate-400"
        />
    </div>
{/if}

<div class="grid h-screen grid-rows-[1fr_auto] gap-y-2 max-h-screen">
    <!-- Module Content -->
    <div class="flex px-1 gap-x-1 h-full overflow-hidden max-h-full" id="split-container">
        <div class="relative flex h-full">
            <!-- Shared Sidebar (Left) - Used by both clustering and downstream modules -->
            {#if currentModule !== "downstream"}
                <aside
                    class="rounded-lg text-sm leading-relaxed flex flex-col min-h-0 bg-white border-stone-300"
                    class:border={!isLeftPanelCollapsed}
                    class:p-4={!isLeftPanelCollapsed}
                    id="left-panel"
                    style={leftPanelStyle}
                    aria-hidden={isLeftPanelCollapsed}
                >
                    {#if !isLeftPanelCollapsed}
                        <div class="flex flex-col h-full min-h-0">
                            <!-- Sidebar header: slice selector + collapse button -->
                            <div
                                class="flex items-center gap-2 border-b border-dashed border-stone-300 pb-2 flex-shrink-0"
                            >
                                <div class="flex-1">
                                    <label
                                        class="sr-only"
                                        for="slice-select-header"
                                        >Slice</label
                                    >
                                    {#if comparisonModeActive}
                                        <select
                                            id="slice-select-header"
                                            class="w-full border border-gray-300 rounded px-3 py-1.5 bg-white text-sm focus:outline-none focus:ring-2 focus:ring-stone-400"
                                            bind:value={currentSlice}
                                        >
                                            {#each allSlices as slice}
                                                <option value={slice}
                                                    >{slice}</option
                                                >
                                            {/each}
                                        </select>
                                    {:else}
                                        <div class="px-3 py-1.5 text-gray-800 bg-gray-50 rounded border border-gray-200 text-sm">
                                            {currentSlice || "N/A"}
                                        </div>
                                    {/if}
                                </div>
                                <button
                                    type="button"
                                    class="flex items-center justify-center h-8 w-8 border border-stone-300 rounded-full bg-white/95 text-gray-600 shadow-sm hover:bg-stone-100 transition-colors focus:outline-none focus:ring-2 focus:ring-slate-400"
                                    on:click={toggleLeftPanel}
                                    aria-label="Collapse sidebar"
                                    aria-controls="left-panel"
                                    title="Collapse sidebar"
                                >
                                    <ChevronLeft size={16} />
                                </button>
                            </div>

                            <!-- Slice info section -->
                            <section class="space-y-1 flex-shrink-0">
                                <button
                                    type="button"
                                    class="w-full flex items-center justify-between text-left text-sm font-semibold text-gray-800"
                                    on:click={() =>
                                        (isSliceSectionOpen = !isSliceSectionOpen)}
                                >
                                    <span>Slice info</span>
                                    <span
                                        class="text-[10px] uppercase tracking-[0.2em] text-gray-500"
                                    >
                                        {isSliceSectionOpen ? "Hide" : "Show"}
                                    </span>
                                </button>
                                {#if isSliceSectionOpen}
                                    <!-- Spatial Info：始终显示 label，无数据时 value 留空 -->
                                    <div
                                        class="pt-1 border-t border-dashed border-stone-300 text-gray-600"
                                    >
                                        {#each sliceInfoDisplayEntries as { key, label, value }}
                                            <div
                                                class="flex justify-between text-xs py-0"
                                            >
                                                <span class="capitalize"
                                                    >{label}:</span
                                                >
                                                <span class="text-gray-800"
                                                    >{value}</span
                                                >
                                            </div>
                                        {/each}
                                    </div>

                                    {#if spotMetricsData && spotMetricsData.length}
                                        <div
                                            class="pt-0.5 border-t border-dashed border-stone-300"
                                        >
                                            <StatisticsPanel {spotMetricsData} />
                                        </div>
                                    {/if}
                                {/if}
                            </section>

                            <!-- Sorting Algorithm Demo Section (hidden, not removed) -->
                            <section class="hidden pt-3 border-t border-dashed border-stone-300 flex-shrink-0">
                                <button
                                    type="button"
                                    class="w-full flex items-center justify-center gap-2 px-3 py-2 text-sm font-semibold text-white bg-blue-500 hover:bg-blue-600 rounded-md transition-colors focus:outline-none focus:ring-2 focus:ring-blue-400"
                                    on:click={() => (showSortingDemo = true)}
                                    title="查看Sankey排序算法演示"
                                >
                                    <svg
                                        xmlns="http://www.w3.org/2000/svg"
                                        width="16"
                                        height="16"
                                        viewBox="0 0 24 24"
                                        fill="none"
                                        stroke="currentColor"
                                        stroke-width="2"
                                        stroke-linecap="round"
                                        stroke-linejoin="round"
                                    >
                                        <polygon points="5 3 19 12 5 21 5 3"></polygon>
                                    </svg>
                                    <span>排序算法演示</span>
                                </button>
                            </section>

                            <!-- Cluster result section -->
                            <section class="pt-3 border-t border-dashed border-stone-300 flex flex-col {isClusterSectionOpen && (currentModule === "downstream" || comparisonModeActive || currentClusterResult) ? 'flex-1 min-h-0' : ''}">
                                <button
                                    type="button"
                                    class="w-full flex items-center justify-between text-left text-sm font-semibold text-gray-800 flex-shrink-0"
                                    on:click={() =>
                                        (isClusterSectionOpen = !isClusterSectionOpen)}
                                >
                                    <span>Cluster result</span>
                                    <span
                                        class="text-[10px] uppercase tracking-[0.2em] text-gray-500"
                                    >
                                        {isClusterSectionOpen ? "Hide" : "Show"}
                                    </span>
                                </button>
                                {#if currentModule === "downstream" || comparisonModeActive}
                                    <!-- In comparison mode OR downstream module: always show cluster result list -->
                                    <!-- Render ClusterManagement but hide if section is closed -->
                                    <div class={isClusterSectionOpen ? "pt-1 flex-1 min-h-0 overflow-y-auto" : "hidden"}>
                                        <ClusterManagement
                                            bind:this={clusterManagementRef}
                                            {baseApi}
                                            {currentSlice}
                                            {clusteringMethods}
                                            currentMethod={uiClusteringMethod}
                                            {n_clusters}
                                            {epoch}
                                            autoSelectOnLoad={false}
                                            verticalLayout={true}
                                            {sortBy}
                                            {sortDirection}
                                            {manualOrder}
                                            hiddenClusterResultIds={hiddenClusterResultIds}
                                            currentModule={currentModule}
                                            on:loadingChange={(e) =>
                                                (isClusterResultsLoading = e.detail.loading)}
                                            on:clusterResultsLoaded={(e) =>
                                                (clusterResultsList =
                                                    e.detail.results ?? [])}
                                            on:orderChange={(e) =>
                                                (manualOrder = e.detail.order)}
                                            on:runCluster={(e) => handleRunCluster(e)}
                                            on:selectClusterResult={(e) =>
                                                handleSelectClusterResult(e)}
                                            on:toggleVisibility={(e) => {
                                                const {
                                                    clusterResultId,
                                                    isHidden,
                                                } = e.detail;
                                                if (isHidden) {
                                                    hiddenClusterResultIds.add(
                                                        clusterResultId,
                                                    );
                                                } else {
                                                    hiddenClusterResultIds.delete(
                                                        clusterResultId,
                                                    );
                                                }
                                                hiddenClusterResultIds =
                                                    hiddenClusterResultIds; // Trigger reactivity
                                            }}
                                        />
                                    </div>
                                {:else if isClusterSectionOpen}
                                        <div class="pt-1 space-y-2 text-xs text-gray-700 {currentClusterResult ? 'flex-1 min-h-0 overflow-y-auto' : ''}">
                                            {#if currentClusterResult}
                                                <!-- First line: back button + ID -->
                                                <div class="flex items-center gap-2">
                                                    <button
                                                        type="button"
                                                        class="inline-flex items-center gap-1 rounded-md border border-slate-300 bg-white px-2 py-1 text-[11px] font-medium text-slate-600 transition hover:bg-slate-100 focus:outline-none focus:ring-1 focus:ring-slate-400"
                                                        on:click={enterComparisonMode}
                                                    >
                                                        <ChevronLeft size={12} />
                                                        <span>Back to all cluster results</span>
                                                    </button>
                                                    <span
                                                        class="font-mono text-[11px] text-slate-800 truncate"
                                                        title={currentClusterResult.cluster_result_id}
                                                    >
                                                        {currentClusterResult.cluster_result_id}
                                                    </span>
                                                </div>

                                                <!-- Parameters: method / k / epoch -->
                                                <div class="space-y-0.5">
                                                    <div>
                                                        <span class="font-semibold text-slate-700">Method:</span>
                                                        <span class="ml-1">
                                                            {currentClusterResult.method || "N/A"}
                                                        </span>
                                                    </div>
                                                    <div class="flex flex-wrap items-center gap-x-3 gap-y-0.5">
                                                        <span>
                                                            <span class="font-semibold text-slate-700">k:</span>
                                                            <span class="ml-1">
                                                                {currentClusterResult.n_clusters ?? "-"}
                                                            </span>
                                                        </span>
                                                        {#if currentClusterResult.epoch}
                                                            <span>
                                                                <span class="font-semibold text-slate-700">Epoch:</span>
                                                                <span class="ml-1">
                                                                    {currentClusterResult.epoch}
                                                                </span>
                                                            </span>
                                                        {/if}
                                                    </div>

                                                    <!-- Date -->
                                                    {#if selectedClusterResultMeta &&
                                                        (selectedClusterResultMeta.updated_at ||
                                                            selectedClusterResultMeta.created_at)}
                                                        <div class="text-[11px] text-slate-500">
                                                            <span class="font-semibold text-slate-600">Date:</span>
                                                            <span class="ml-1">
                                                                {new Date(
                                                                    selectedClusterResultMeta.updated_at ||
                                                                        selectedClusterResultMeta.created_at,
                                                                ).toLocaleString()}
                                                            </span>
                                                        </div>
                                                    {/if}
                                                </div>

                                                <!-- Spots & log table moved into sidebar -->
                                                <div class="mt-2 border-t border-dashed border-stone-300 pt-2">
                                                    <div class="h-[480px] max-h-[60vh] rounded border border-stone-200 overflow-hidden">
                                                        <ClusterSpotLogPanel
                                                            {spatialData}
                                                            {clusterColorScale}
                                                            {allLog}
                                                            {hoveredBarcode}
                                                            on:spotClick={(e) =>
                                                                handleSpotClick(e.detail)}
                                                        />
                                                    </div>
                                                </div>
                                            {:else}
                                                <p class="text-xs text-slate-500">
                                                    No cluster result selected.
                                                </p>
                                            {/if}
                                        </div>
                                {/if}
                            </section>

                            <!-- Downstream analysis section -->
                            <section class="pt-3 border-t border-dashed border-stone-300 flex-shrink-0">
                                <div class="flex items-center justify-between gap-2 pb-2 border-b border-dashed border-stone-300">
                                    <span class="text-sm font-semibold text-gray-800">Downstream analysis</span>
                                    {#if currentClusterResultId && currentClusterResultId !== "default"}
                                        <button
                                            type="button"
                                            class="inline-flex items-center gap-1.5 rounded-md border border-slate-300 bg-slate-50 px-2 py-1 text-[11px] font-medium text-slate-700 transition hover:bg-slate-100 focus:outline-none focus:ring-1 focus:ring-slate-400"
                                            on:click={enterDownstreamModule}
                                            title="Open downstream analysis"
                                        >
                                            <ExternalLink size={12} />
                                            <span>Open</span>
                                        </button>
                                    {:else}
                                        <span class="text-xs text-slate-500">
                                            Select a result first
                                        </span>
                                    {/if}
                                </div>
                            </section>
                        </div>
                    {:else}
                        <div
                            class="flex flex-col items-center justify-center gap-3 h-full"
                        >
                            <span
                                class="text-[10px] font-semibold tracking-[0.35em] uppercase text-gray-500 [writing-mode:vertical-rl] rotate-180"
                            >
                                Slice Info
                            </span>
                            <button
                                type="button"
                                class="flex items-center justify-center h-9 w-9 border border-stone-300 rounded-full bg-white text-gray-600 shadow-sm hover:bg-stone-100 transition-colors focus:outline-none focus:ring-2 focus:ring-slate-400"
                                on:click={toggleLeftPanel}
                                aria-label="Expand sidebar"
                                aria-controls="left-panel"
                                title="Expand sidebar"
                            >
                                <ChevronRight size={16} />
                            </button>
                        </div>
                    {/if}
                </aside>
            {/if}
            </div>
            <!-- Main Content Area - Changes based on current module -->
            <div
                class="flex flex-col gap-y-2 h-full w-full min-h-0"
                id="center-panel"
                style="flex: 1 1 auto; min-width: 320px;"
            >
                {#if currentModule === "clustering"}
                    <!-- Clustering Module Content -->
                    <!-- In the center panel, keep spatial plot clean: no header above it -->
                    <div class="flex gap-x-1 flex-1 min-h-0 min-w-0 overflow-hidden" style="width: 100%; max-width: 100%;">
                        {#if comparisonModeActive}
                        <div class="flex-1 min-h-0" style="overflow: hidden;">
                            <ClusterResultComparison
                                {baseApi}
                                {currentSlice}
                                {spatialData}
                                {image}
                                clusterResults={sortedClusterResultsList}
                                allClusterResults={clusterResultsList}
                                hiddenResultIds={hiddenClusterResultIds}
                                scrollSource={clusterResultsScroller}
                                verticalLayout={true}
                                {sortBy}
                                {sortDirection}
                                selectedResultId={currentClusterResultId}
                                {isLeftPanelCollapsed}
                                on:sortChange={(e) => {
                                    const newSortBy = e.detail.sortBy;
                                    const newDirection = e.detail.sortDirection ?? "default";
                                    // If switching from custom to another sort, clear manual order
                                    if (sortBy === "custom" && newSortBy !== "custom") {
                                        manualOrder = null;
                                    }
                                    sortBy = newSortBy;
                                    sortDirection = newDirection;
                                }}
                                on:loadingChange={(e) =>
                                    (isComparisonLoading = e.detail.loading)}
                                on:flowSegmentClick={(e) =>
                                    handleFlowSegmentClick(e.detail)}
                                on:selectClusterResult={(e) =>
                                    handleSelectClusterResult(e)}
                            />
                        </div>
                    {:else}
                        <section
                            class="flex-1 flex flex-col min-h-0 min-w-0"
                            bind:this={splitContainer}
                            style="height: 100%; width: 100%; max-width: 100%;"
                        >
                            {#if lassoSelected}
                                <!-- Lasso mode: left = square spatial preview + UMAP, right = Count Distribution + Lassomode stacked -->
                                <div class="flex flex-1 min-h-0 min-w-0 gap-3 items-stretch" style="width: 100%; max-width: 100%;">
                                    <!-- Left: spatial preview (square) + UMAP below -->
                                    <div class="flex flex-col min-h-0 flex-none w-[520px] xl:w-[580px] flex-shrink-0">
                                        <!-- Spatial preview (kept square, but smaller than normal) -->
                                        <div class="flex-none flex items-center justify-center">
                                            <div
                                                class="border border-stone-300 rounded-lg p-1"
                                                style="width: 100%; aspect-ratio: 1 / 1;"
                                            >
                                                <Plot
                                                    key={currentModule}
                                                    {spatialData}
                                                    originalSpatialData={originalSpatialData}
                                                    {imageUrl}
                                                    {image}
                                                    {clusterColorScale}
                                                    {baseApi}
                                                    {currentSlice}
                                                    {currentClusterResultId}
                                                    {lassoSelected}
                                                    {clickedInfo}
                                                    {hoveredBarcode}
                                                    {lassoHover}
                                                    {lassoClearSignal}
                                                    on:spotClick={(e) =>
                                                        handleSpotClick(e.detail)}
                                                    on:clusterUpdate={(e) =>
                                                        handleClusterUpdate(e.detail)}
                                                    on:hover={(e) =>
                                                        handleHoverEvent(e.detail)}
                                                    on:plotRendered={() => {
                                                        // Plot has been rendered with colors, can cancel loading
                                                        if (loadingTimeoutId) {
                                                            clearTimeout(loadingTimeoutId);
                                                            loadingTimeoutId = null;
                                                        }
                                                        if (isLoadingClusterResult) {
                                                            isLoadingClusterResult = false;
                                                        }
                                                    }}
                                                />
                                            </div>
                                        </div>

                                        <!-- Below preview: UMAP only -->
                                        <div
                                            class="flex-1 min-h-[160px] mt-2 border border-stone-300 rounded-lg p-2 overflow-hidden"
                                            role="region"
                                            aria-label="UMAP panel"
                                            on:mouseleave={() => {
                                                // 如果没有选中或lasso，清除hover状态
                                                if (!clickedInfo && !lassoSelected) {
                                                    handleHoverEvent({
                                                        barcode: -1,
                                                        from: "umap",
                                                        persistent: false,
                                                    });
                                                }
                                            }}
                                        >
                                            <OverviewUmapPanel
                                                {clusterColorScale}
                                                {umapData}
                                                refreshToken={clusterColorScaleVersion}
                                                {hoveredBarcode}
                                                {clickedInfo}
                                                {lassoSelected}
                                                {lassoHover}
                                                on:hover={(e) =>
                                                    handleHoverEvent(e.detail)}
                                            />
                                        </div>
                                    </div>

                                    <!-- Right: Count Distribution (top) + Lassomode (bottom) stacked in one column -->
                                    <div
                                        class="flex-1 min-h-[260px] min-w-0 flex flex-col gap-2"
                                        style="max-width: 100%; overflow: hidden;"
                                    >
                                        <!-- Top: Count Distribution (30% height) -->
                                        <div
                                            class="border border-stone-300 rounded-lg p-3 overflow-auto min-w-0"
                                            style="flex: 0 0 30%;"
                                        >
                                            <OverviewClusterPanel
                                                {clusterColorScale}
                                                {clusterDistribution}
                                                refreshToken={clusterColorScaleVersion}
                                                {baseApi}
                                                {currentSlice}
                                                {currentClusterResultId}
                                                {allClustersMarkerGenes}
                                                {cellTypeAnnotations}
                                                on:clusterClick={(e) =>
                                                    handleClusterClick(e.detail)}
                                            />
                                        </div>

                                        <!-- Bottom: Lassomode detail / reclustering (70% height) -->
                                        <div
                                            class="border border-stone-300 rounded-lg p-3 overflow-hidden flex flex-col min-w-0"
                                            style="flex: 0 0 70%;"
                                        >
                                            <div class="flex-1 min-h-0 min-w-0">
                                                <Lassomode
                                                    {clickedInfo}
                                                    {baseApi}
                                                    {currentSlice}
                                                    {clusterColorScale}
                                                    {previewUrl}
                                                    {spatialData}
                                                    {hoveredBarcode}
                                                    initCurrentMethod={currentClusterResult?.method ||
                                                        currentMethod}
                                                    initCurrentClusterResultId={currentClusterResultId}
                                                    on:acceptRecluster={(e) =>
                                                        handleClusterUpdate(
                                                            e.detail,
                                                        )}
                                                    on:previewRecluster={(e) =>
                                                        handlePreviewRecluster(
                                                            e.detail,
                                                        )}
                                                    on:batchPreviewRecluster={(e) =>
                                                        handleBatchPreviewRecluster(
                                                            e.detail,
                                                        )}
                                                    on:completeRecluster={handleCompleteRecluster}
                                                    on:reclustered={(e) => {
                                                        // Save original spatialData when recluster results are returned
                                                        saveOriginalSpatialData();
                                                    }}
                                                    on:lassoHover={(e) => {
                                                        lassoHover = e.detail;
                                                    }}
                                                    on:clear={handleLassoClear}
                                                />
                                            </div>
                                        </div>
                                    </div>
                                </div>
                            {:else}
                                <!-- Normal mode: middle square spatial plot, remaining width for right column (distribution + UMAP) -->
                                <div class="flex flex-1 min-h-0 gap-3 w-full items-stretch">
                                    <!-- Middle: spatial plot with width ≈ height (square-ish) -->
                                    <div class="flex-none h-full flex items-center justify-center">
                                        <div
                                            class="border border-stone-300 rounded-lg p-1"
                                            style="height: 100%; aspect-ratio: 1 / 1; max-width: 100%;"
                                        >
                                            <Plot
                                                key={currentModule}
                                                {spatialData}
                                                {imageUrl}
                                                {image}
                                                {clusterColorScale}
                                                {baseApi}
                                                {currentSlice}
                                                {currentClusterResultId}
                                                {lassoSelected}
                                                {clickedInfo}
                                                {hoveredBarcode}
                                                {lassoHover}
                                                {lassoClearSignal}
                                                on:spotClick={(e) =>
                                                    handleSpotClick(e.detail)}
                                                on:clusterUpdate={(e) =>
                                                    handleClusterUpdate(e.detail)}
                                                on:hover={(e) =>
                                                    handleHoverEvent(e.detail)}
                                                on:plotRendered={() => {
                                                    // Plot has been rendered with colors, can cancel loading
                                                    if (loadingTimeoutId) {
                                                        clearTimeout(loadingTimeoutId);
                                                        loadingTimeoutId = null;
                                                    }
                                                    if (isLoadingClusterResult) {
                                                        isLoadingClusterResult = false;
                                                    }
                                                }}
                                            />
                                        </div>
                                    </div>

                                    <!-- Right: cluster distribution (top) + UMAP (bottom) in same column, takes remaining width -->
                                    <div class="flex-1 min-w-[260px] min-h-0 flex flex-col gap-2">
                                        <div
                                            class="flex-1 min-h-[160px] border border-stone-300 rounded-lg p-3 overflow-auto"
                                        >
                                            <OverviewClusterPanel
                                                {clusterColorScale}
                                                {clusterDistribution}
                                                refreshToken={clusterColorScaleVersion}
                                                {baseApi}
                                                {currentSlice}
                                                {currentClusterResultId}
                                                {allClustersMarkerGenes}
                                                {cellTypeAnnotations}
                                                on:clusterClick={(e) =>
                                                    handleClusterClick(e.detail)}
                                            />
                                        </div>
                                        <div
                                            class="flex-1 min-h-[140px] border border-stone-300 rounded-lg p-2 overflow-hidden"
                                            role="region"
                                            aria-label="UMAP panel"
                                            on:mouseleave={() => {
                                                // 如果没有选中或lasso，清除hover状态
                                                if (!clickedInfo && !lassoSelected) {
                                                    handleHoverEvent({
                                                        barcode: -1,
                                                        from: "umap",
                                                        persistent: false,
                                                    });
                                                }
                                            }}
                                        >
                                            <OverviewUmapPanel
                                                {clusterColorScale}
                                                {umapData}
                                                refreshToken={clusterColorScaleVersion}
                                                {hoveredBarcode}
                                                {clickedInfo}
                                                {lassoSelected}
                                                {lassoHover}
                                                on:hover={(e) =>
                                                    handleHoverEvent(e.detail)}
                                            />
                                        </div>
                                    </div>
                                </div>
                            {/if}
                        </section>
                    {/if}
                    </div>
                {:else if currentModule === "downstream"}
                    <!-- Downstream Analysis Module Content -->
                    <div class="flex-1 min-h-0 overflow-hidden flex flex-col">
                        <div class="flex items-center justify-between gap-3 px-4 py-3 border-b border-gray-300 bg-white flex-shrink-0">
                            <div class="flex items-center gap-3">
                                <button
                                    type="button"
                                    class="inline-flex items-center justify-center h-8 w-8 rounded-md border border-slate-300 bg-white text-slate-600 transition hover:bg-slate-100 focus:outline-none focus:ring-1 focus:ring-slate-400"
                                    on:click={enterComparisonMode}
                                    title="Close downstream analysis"
                                    aria-label="Close downstream analysis"
                                >
                                    <X size={16} />
                                </button>
                                <h2 class="text-lg font-semibold text-gray-800">Downstream Analysis</h2>
                            </div>
                            <button
                                type="button"
                                class="inline-flex items-center justify-center h-8 w-8 rounded-md border border-slate-300 bg-white text-slate-600 transition hover:bg-slate-100 focus:outline-none focus:ring-1 focus:ring-slate-400"
                                on:click={() => {
                                    // Dispatch event to show analysis selection dialog
                                    document.dispatchEvent(new CustomEvent('showAnalysisSelection'));
                                }}
                                title="Add analysis"
                                aria-label="Add analysis"
                            >
                                <Plus size={16} />
                            </button>
                        </div>
                        <div class="flex-1 min-h-0 overflow-hidden">
                            {#if currentModule === "downstream"}
                                <!-- Debug: Log spatialData when rendering DownstreamAnalysis -->
                                {#if spatialData}
                                    {@const _ = console.log("🔍 App.svelte: Passing spatialData to DownstreamAnalysis", {
                                        isArray: Array.isArray(spatialData),
                                        length: Array.isArray(spatialData) ? spatialData.length : 'N/A',
                                        hasImage: !!image,
                                        hasClusterColorScale: !!clusterColorScale && typeof clusterColorScale === 'function',
                                        currentClusterResultId
                                    })}
                                {/if}
                            {/if}
                            <DownstreamAnalysis
                                {baseApi}
                                {availableClusters}
                                {clusterColorScale}
                                clusterColorScaleVersion={clusterColorScaleVersion}
                                {currentSlice}
                                {currentClusterResultId}
                                currentClusterResult={currentClusterResult}
                                selectedClusterResultMeta={selectedClusterResultMeta}
                                {spatialData}
                                {image}
                                isLoadingClusterResult={isLoadingClusterResult}
                                on:hover={(e) => {
                                    hoveredBarcode = {
                                        barcode: e.detail.barcode,
                                        from: e.detail.from,
                                    };
                                }}
                                on:plotRendered={() => {
                                    // Plot has been rendered with colors, can cancel loading
                                    if (loadingTimeoutId) {
                                        clearTimeout(loadingTimeoutId);
                                        loadingTimeoutId = null;
                                    }
                                    if (isLoadingClusterResult) {
                                        isLoadingClusterResult = false;
                                    }
                                }}
                                on:updateColorScale={async (e) => {
                                    const { clusters, clusterResultId, sliceId } = e.detail;
                                    console.log("🎨 App.svelte: Updating color scale from DownstreamAnalysis", {
                                        clusters,
                                        clusterResultId,
                                        sliceId
                                    });
                                    // Update color scale with the clusters from downstream analysis
                                    await fetchClusterColorMapping(
                                        sliceId,
                                        clusterResultId,
                                        clusters,
                                    );
                                    // Ensure color scale is built with all clusters
                                    buildClusterColorScale(clusters, lastColorMapping);
                                    // Don't cancel loading here - wait for plot to render with colors
                                }}
                            ></DownstreamAnalysis>
                        </div>
                    </div>
                {/if}
            </div>
        </div>
    <!-- Footer -->
    <!-- <footer class=" text-center">@2025.5</footer> -->
</div>

<style>
    /* Split.js styles */
    :global(.gutter) {
        background-color: #e5e7eb;
        background-repeat: no-repeat;
        background-position: 50%;
        cursor: row-resize;
        transition: background-color 0.2s;
    }

    :global(.gutter:hover) {
        background-color: #9ca3af;
    }

    :global(.gutter.gutter-vertical) {
        cursor: row-resize;
        background-image: url("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAUAAAAeCAYAAADkftS9AAAAIklEQVQoU2M4c+bMfxAGAgYYmwGrIIiDjrELjpo5aiZeMwF+yNnOs5KSvgAAAABJRU5ErkJggg==");
    }

    :global(.gutter.gutter-vertical:hover) {
        background-image: url("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAUAAAAeCAYAAADkftS9AAAAIklEQVQoU2M4c+bMfxAGAgYYmwGrIIiDjrELjpo5aiZeMwF+yNnOs5KSvgAAAABJRU5ErkJggg==");
    }

    aside {
        scrollbar-width: none;
    }
    aside:hover {
        scrollbar-width: thin;
    }
    aside::-webkit-scrollbar {
        width: 0;
        height: 0;
    }
    aside:hover::-webkit-scrollbar {
        width: 8px;
    }
    aside:hover::-webkit-scrollbar-track {
        background: transparent;
    }
    aside:hover::-webkit-scrollbar-thumb {
        background-color: rgba(100, 100, 100, 0.4);
        border-radius: 4px;
    }
</style>

{#if showSortingDemo}
    <SankeySortingDemo on:close={() => (showSortingDemo = false)} />
{/if}

