<script>
    import { createEventDispatcher, onMount, tick } from "svelte";
    import Plotly from "plotly.js-dist-min";
    import * as d3 from "d3";
    import { Tabs } from "@skeletonlabs/skeleton-svelte";
    import { ProgressRing } from "@skeletonlabs/skeleton-svelte";
    import { Segment } from "@skeletonlabs/skeleton-svelte";
    import DownstreamPlot from "./downstreamPlot.svelte";
    import DeconvolutionAnalysis from "./deconvolutionAnalysis.svelte";
    import { marked } from "marked";
    
    // Configure marked options
    marked.setOptions({
        breaks: true, // Convert \n to <br>
        gfm: true, // GitHub Flavored Markdown
    });
    
    // Convert Markdown to HTML using marked library
    function markdownToHtml(markdown) {
        if (!markdown) return "";
        return marked.parse(markdown);
    }

    export let baseApi;
    export let availableClusters;
    export let clusterColorScale;
    export let clusterColorScaleVersion = 0;
    export let currentSlice;
    export let currentClusterResultId;
    export let currentClusterResult = null;
    export let selectedClusterResultMeta = null;
    export let isLoadingClusterResult = false; // Loading state when switching cluster results
    // Reuse spatial data and image from clustering module if available
    export let spatialData = [];
    export let image = null;
    // DEG data will be loaded on demand, not passed as props
    let clusterGeneExpression = null;
    let clusterGeneDot = null;
    let hoveredBarcode = null;
    let selectedRegionCount = 0; // Track number of selected regions for display
    let selectedBarcodes = []; // Track selected barcodes for radial plot
    let downstreamPlotInstance = null; // Reference to DownstreamPlot component
    let showAttentionFlow = false; // Control attention flow display
    let radialPlotData = null; // Data for radial plot
    let loadingRadialPlot = false; // Loading state for radial plot
    let selectedCenterBarcode = null; // Selected center barcode for radial plot
    let availableCenters = []; // List of available center barcodes
    let loadingCenters = false; // Loading state for centers list
    let selectedCenterIndex = null; // Selected center index for radial plot

    const dispatch = createEventDispatcher();

    // Analysis types
    const analysisTypes = [
        {
            id: "svg",
            name: "SVG Analysis",
            description: "Spatially Variable Gene Analysis",
        },
        {
            id: "spatial",
            name: "Spatial Communication",
            description: "Cell-Cell Communication Analysis",
        },
        {
            id: "deg",
            name: "DEG Analysis",
            description: "Differentially Expressed Genes Analysis",
        },
        {
            id: "deconvolution",
            name: "Deconvolution Analysis",
            description: "Cell Type Deconvolution Analysis",
        },
        {
            id: "neighborhood",
            name: "Neighborhood Analysis",
            description: "Attention Flow Neighborhood Analysis (hbrc only)",
        },
    ];

    let selectedAnalysis = null;
    let loading = false;
    let analysisData = {};
    let errorMessage = null;

    // New layout: track loaded analyses and show dialog
    let loadedAnalyses = []; // Array of { id, type, name, data, ... }
    let showAnalysisDialog = false;
    let pendingSVGAnalysis = false; // Track if we're waiting for SVG cluster selection
    let selectedSVGClusterInDialog = null; // Track cluster selection for SVG analysis in dialog

    // Track which clusters already have SVG analyses
    $: svgClustersInUse = loadedAnalyses
        .filter((a) => a.type === "svg" && a.selectedCluster != null)
        .map((a) => `${a.selectedCluster}`);
    $: svgAllClustersUsed =
        Array.isArray(availableClusters) &&
        availableClusters.length > 0 &&
        svgClustersInUse.length > 0 &&
        availableClusters.every((c) => svgClustersInUse.includes(`${c}`));

    // SVG Analysis variables
    const GeneMode = ["Bar", "Sankey"];
    let currentGeneMode = "Bar";
    let currentCluster = "1.0";
    let selectedSVGCluster = null; // Track which cluster is selected for SVG analysis
    let barChartDiv;
    let sankeyDiv;
    let hvg = {};

    // Spatial Communication variables
    const InteractionMode = ["Cluster", "Ligand-Receptor"];
    let currentInteractionMode = "Cluster";
    let cellChatDivNumber;
    let cellChatDivStrength;
    let ligandReceptorDivNumber;
    let ligandReceptorDivStrength;
    let cellChat = null;

    // DEG Analysis variables
    let clusterGeneExpressionDiv;
    let clusterGeneDotDiv;
    const DEGChartMode = ["Heatmap", "Dotplot"];
    let currentDEGMode = "Heatmap";

    // Deconvolution Analysis variables
    let deconvolutionData = null;
    let selectedCellTypes = []; // Selected cell types from hierarchy chart (max 6)
    const MAX_SELECTED_CELL_TYPES = 6;
    let loadingDeconvolution = false;

    function getTopNMatrix(matrix, N) {
        let links = [];
        for (let i = 0; i < matrix.length; i++) {
            for (let j = 0; j < matrix[i].length; j++) {
                if (i !== j && matrix[i][j] > 0) {
                    links.push({ i, j, value: matrix[i][j] });
                }
            }
        }
        links.sort((a, b) => b.value - a.value);
        let topLinks = links.slice(0, N);
        let newMatrix = matrix.map((row) => row.map(() => 0));
        topLinks.forEach(({ i, j, value }) => {
            newMatrix[i][j] = value;
        });
        return newMatrix;
    }

    // Filter out clusters with no communication (all zeros in both rows and columns)
    function filterEmptyClusters(matrix, names) {
        const n = matrix.length;
        const hasCommunication = Array(n).fill(false);

        // Check which clusters have any communication (outgoing or incoming)
        for (let i = 0; i < n; i++) {
            const outgoing = matrix[i].some((val) => val > 0);
            const incoming = matrix.some((row) => row[i] > 0);
            hasCommunication[i] = outgoing || incoming;
        }

        // Get indices of clusters with communication
        const activeIndices = [];
        for (let i = 0; i < n; i++) {
            if (hasCommunication[i]) {
                activeIndices.push(i);
            }
        }

        if (activeIndices.length === 0) {
            return { matrix: [], names: [] };
        }

        // Build new matrix and names with only active clusters
        const newMatrix = [];
        const newNames = [];

        activeIndices.forEach((oldIdx) => {
            newNames.push(names[oldIdx]);
        });

        activeIndices.forEach((oldIdx) => {
            const newRow = [];
            activeIndices.forEach((oldJdx) => {
                newRow.push(matrix[oldIdx][oldJdx]);
            });
            newMatrix.push(newRow);
        });

        return { matrix: newMatrix, names: newNames };
    }

    function buildQuery(params = {}) {
        const searchParams = new URLSearchParams();
        Object.entries(params).forEach(([key, value]) => {
            if (value !== undefined && value !== null && value !== "") {
                searchParams.append(key, value);
            }
        });
        const queryString = searchParams.toString();
        return queryString ? `?${queryString}` : "";
    }

    async function loadSVGAnalysis(cluster = null, forceReload = false) {
        const targetCluster = cluster || selectedSVGCluster || currentCluster;
        console.log(
            "loadSVGAnalysis called, targetCluster:",
            targetCluster,
            "forceReload:",
            forceReload,
            "currentSlice:",
            currentSlice,
            "currentClusterResultId:",
            currentClusterResultId,
        );

        if (!targetCluster) {
            console.warn("loadSVGAnalysis: No target cluster provided");
            errorMessage = "Please select a cluster first";
            return Promise.reject(new Error("Please select a cluster first"));
        }

        if (!currentSlice || !currentClusterResultId) {
            console.error("loadSVGAnalysis: Missing required parameters", {
                currentSlice,
                currentClusterResultId,
            });
            errorMessage =
                "Missing slice_id or cluster_result_id. Please ensure you have selected a slice and cluster result.";
            return Promise.reject(
                new Error("Missing slice_id or cluster_result_id"),
            );
        }

        // Don't reload if data already exists for this cluster (unless forceReload is true)
        if (!forceReload && hvg[targetCluster]) {
            console.log(
                "loadSVGAnalysis: Data already exists for cluster",
                targetCluster,
                "skipping API call. Use forceReload=true to reload.",
            );
            return Promise.resolve();
        }

        loading = true;
        errorMessage = null;
        try {
            const hvgQuery = buildQuery({
                slice_id: currentSlice,
                cluster_result_id: currentClusterResultId,
                cluster: targetCluster,
            });
            const apiUrl = `${baseApi}/hvg-enrichment-cluster${hvgQuery}`;
            console.log("loadSVGAnalysis: Fetching from", apiUrl);
            const hvgRes = await fetch(apiUrl);

            if (!hvgRes.ok) {
                const errorText = await hvgRes.text();
                throw new Error(
                    `HTTP ${hvgRes.status}: ${errorText || hvgRes.statusText}`,
                );
            }

            const hvgData = await hvgRes.json();

            const rawClusterKey = Object.keys(hvgData)[0];
            const enrichmentResults = hvgData[rawClusterKey];

            hvg = {
                ...hvg,
                [targetCluster]: enrichmentResults,
            };

            analysisData.svg = hvg;
            currentCluster = targetCluster;
            console.log("SVG Analysis loaded for cluster:", targetCluster);
        } catch (error) {
            console.error("Error loading SVG analysis:", error);
            errorMessage =
                error.message ||
                "Failed to load SVG analysis. Please check your network connection and try again.";
        } finally {
            loading = false;
        }
    }

    async function loadSpatialCommunication() {
        // Don't reload if data already exists
        if (cellChat) {
            console.log(
                "Spatial Communication data already cached, skipping reload",
            );
            return;
        }

        loading = true;
        errorMessage = null;
        try {
            const cellChatQuery = buildQuery({
                slice_id: currentSlice,
                cluster_result_id: currentClusterResultId,
            });
            const cellChatRes = await fetch(
                `${baseApi}/cellchat${cellChatQuery}`,
            );

            if (!cellChatRes.ok) {
                const errorText = await cellChatRes.text();
                throw new Error(
                    `HTTP ${cellChatRes.status}: ${errorText || cellChatRes.statusText}`,
                );
            }

            const cellChatData = await cellChatRes.json();

            cellChat = JSON.parse(JSON.stringify(cellChatData));
            analysisData.spatial = cellChat;
            console.log("Spatial Communication loaded:", cellChat);
        } catch (error) {
            console.error("Error loading spatial communication:", error);
            errorMessage =
                error.message ||
                "Failed to load spatial communication analysis. Please check your network connection and try again.";
        } finally {
            loading = false;
        }
    }

    async function loadDEGAnalysis() {
        // Don't reload if data already exists
        if (clusterGeneExpression && clusterGeneDot) {
            console.log("DEG Analysis data already cached, skipping reload");
            return;
        }

        loading = true;
        try {
            const sharedQuery = buildQuery({
                slice_id: currentSlice,
                cluster_result_id: currentClusterResultId,
            });
            const [expressionRes, dotplotRes] = await Promise.all([
                fetch(`${baseApi}/cluster-gene-expression${sharedQuery}`),
                fetch(`${baseApi}/cluster_gene_dotplot${sharedQuery}`),
            ]);

            const expressionData = await expressionRes.json();
            const dotplotData = await dotplotRes.json();

            clusterGeneExpression = expressionData;
            clusterGeneDot = dotplotData;

            analysisData.deg = {
                expression: clusterGeneExpression,
                dot: clusterGeneDot,
            };
            console.log("DEG Analysis loaded:", analysisData.deg);
        } catch (error) {
            console.error("Error loading DEG analysis:", error);
        } finally {
            loading = false;
        }
    }

    async function loadDeconvolutionAnalysis() {
        // Don't reload if data already exists
        if (deconvolutionData) {
            console.log("Deconvolution data already cached, skipping reload");
            return;
        }

        if (!currentSlice || !currentClusterResultId) {
            errorMessage =
                "Missing slice_id or cluster_result_id. Please ensure you have selected a slice and cluster result.";
            return;
        }

        loadingDeconvolution = true;
        errorMessage = null;
        selectedCellTypes = []; // Reset selected cell types when loading new data
        try {
            const deconvQuery = buildQuery({
                slice_id: currentSlice,
                cluster_result_id: currentClusterResultId,
            });
            const response = await fetch(
                `${baseApi}/deconvolution${deconvQuery}`,
            );

            if (!response.ok) {
                const errorText = await response.text();
                throw new Error(
                    `HTTP ${response.status}: ${errorText || response.statusText}`,
                );
            }

            const data = await response.json();

            // Check if data is valid
            if (!data || !data.cell_types || data.cell_types.length === 0) {
                throw new Error(
                    "No deconvolution data available. Please ensure clustering has been completed.",
                );
            }

            deconvolutionData = data;
            analysisData.deconvolution = deconvolutionData;
            
            // Default: select first 6 coarse (level 1) cell types
            if (data.hierarchy && data.hierarchy.length > 0) {
                const coarseTypes = data.hierarchy
                    .filter(node => node.type === "coarse")
                    .slice(0, MAX_SELECTED_CELL_TYPES)
                    .map(node => node.name);
                selectedCellTypes = coarseTypes;
                console.log("Default selected cell types:", selectedCellTypes);
            }
            
            console.log("Deconvolution Analysis loaded:", deconvolutionData);
        } catch (error) {
            console.error("Error loading deconvolution analysis:", error);
            errorMessage =
                error.message ||
                "Failed to load deconvolution analysis. Please check your network connection and try again.";
            deconvolutionData = null;
        } finally {
            loadingDeconvolution = false;
        }
    }

    function selectAnalysis(analysisId) {
        selectedAnalysis = analysisId;

        // SVG analysis: don't load immediately, wait for cluster selection
        // if (analysisId === "svg" && !analysisData.svg) {
        //     loadSVGAnalysis();
        // } else
        if (analysisId === "spatial" && !analysisData.spatial) {
            loadSpatialCommunication();
        } else if (analysisId === "deg" && !analysisData.deg) {
            loadDEGAnalysis();
        } else if (
            analysisId === "deconvolution" &&
            !analysisData.deconvolution
        ) {
            loadDeconvolutionAnalysis();
        }
    }

    function startSVGAnalysis() {
        console.log(
            "startSVGAnalysis called, selectedSVGCluster:",
            selectedSVGCluster,
        );
        console.log(
            "currentSlice:",
            currentSlice,
            "currentClusterResultId:",
            currentClusterResultId,
        );
        if (selectedSVGCluster) {
            currentCluster = selectedSVGCluster;
            console.log(
                "Calling loadSVGAnalysis with cluster:",
                selectedSVGCluster,
            );
            // Force reload by passing true as second parameter
            loadSVGAnalysis(selectedSVGCluster, true);
        } else {
            console.warn("No cluster selected for SVG analysis");
            errorMessage = "Please select a cluster first";
        }
    }

    function resetAnalysis() {
        // Only reset the selected analysis state, keep cached data
        selectedAnalysis = null;
        selectedSVGCluster = null;
        errorMessage = null;
        // Note: Keep all loaded data cached (hvg, cellChat, clusterGeneExpression, clusterGeneDot)
        // Only reset when slice or cluster_result_id changes
    }

    function clearAnalysisCache() {
        // Clear all cached analysis data (called when slice or cluster_result_id changes)
        analysisData = {};
        hvg = {};
        cellChat = null;
        clusterGeneExpression = null;
        clusterGeneDot = null;
        deconvolutionData = null;
        errorMessage = null;
        selectedSVGCluster = null;
        currentCluster = availableClusters?.length
            ? `${availableClusters[0]}`
            : "1.0";
        radialPlotData = null;
        selectedBarcodes = [];
        availableCenters = [];
        selectedCenterBarcode = null;
        selectedCenterIndex = null;
    }

    async function loadAvailableCenters(barcodes = null) {
        if (currentSlice !== "hbrc") {
            return;
        }

        loadingCenters = true;
        try {
            let response;

            if (barcodes && barcodes.length > 0) {
                // 如果有套索选择区域，获取区域内有效的 centers
                // 使用 POST 请求发送候选 barcodes
                response = await fetch(
                    `${baseApi}/attention-flow-centers?slice_id=${currentSlice}`,
                    {
                        method: "POST",
                        headers: {
                            "Content-Type": "application/json",
                        },
                        body: JSON.stringify(barcodes),
                    },
                );

                if (!response.ok) {
                    throw new Error(`HTTP error! status: ${response.status}`);
                }

                const data = await response.json();
                // 仅显示筛选后的 centers
                availableCenters = (data.centers || []).map(
                    (barcode, index) => ({
                        index: index,
                        barcode: barcode,
                        neighborCount: 0, // Will be loaded when center is selected
                    }),
                );

                // 如果有可用的 centers，自动选择第一个并显示
                if (availableCenters.length > 0) {
                    selectedCenterBarcode = availableCenters[0].barcode;
                    // Auto-load the plot for the first center
                    loadRadialPlotForCenter(selectedCenterBarcode);
                } else {
                    radialPlotData = null;
                    selectedCenterBarcode = null;
                }
            } else {
                // 如果没有套索，使用 GET 请求加载整个切片的所有 centers
                response = await fetch(
                    `${baseApi}/attention-flow-centers?slice_id=${currentSlice}`,
                    {
                        method: "POST", // 统一使用 POST 也可以，body 传空列表或 null
                        headers: {
                            "Content-Type": "application/json",
                        },
                        body: JSON.stringify([]), // 空列表表示不筛选
                    },
                );

                if (!response.ok) {
                    throw new Error(`HTTP error! status: ${response.status}`);
                }

                const data = await response.json();
                availableCenters = (data.centers || []).map(
                    (barcode, index) => ({
                        index: index,
                        barcode: barcode,
                        neighborCount: 0, // Will be loaded when center is selected
                    }),
                );
                radialPlotData = null; // Will be loaded when center is selected
                // Reset selection for full slice mode to avoid overwhelming user
                selectedCenterIndex = null;
                selectedCenterBarcode = null;
            }
        } catch (error) {
            console.error("Failed to load available centers:", error);
            errorMessage = `Failed to load available centers: ${error.message}`;
            availableCenters = [];
        } finally {
            loadingCenters = false;
        }
    }

    async function loadRadialPlotForCenter(centerBarcode) {
        if (!centerBarcode || currentSlice !== "hbrc") {
            return;
        }

        loadingRadialPlot = true;
        try {
            const response = await fetch(
                `${baseApi}/attention-flow-radial?slice_id=${currentSlice}&cluster_result_id=${currentClusterResultId}`,
                {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                    },
                    body: JSON.stringify([centerBarcode]),
                },
            );

            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }

            const data = await response.json();
            radialPlotData = data;
        } catch (error) {
            console.error("Failed to load radial plot data:", error);
            errorMessage = `Failed to load radial plot data: ${error.message}`;
            radialPlotData = null;
        } finally {
            loadingRadialPlot = false;
            // 等模板切到 radial-plot-container 这支分支
            await tick();
            if (
                radialPlotData &&
                radialPlotData.centers &&
                radialPlotData.centers.length > 0
            ) {
                drawRadialPlot();
            }
        }
    }

    function showRadialPlot() {
        if (selectedCenterBarcode === null) {
            return;
        }

        // Load radial plot data for selected center
        loadRadialPlotForCenter(selectedCenterBarcode);
    }

    // Load centers when neighborhood analysis is selected or barcodes change
    // Use a string key to detect changes and avoid infinite loops with loadingCenters
    let lastLoadedKey = "";

    // Auto-reset neighborhood analysis if lasso selection is removed
    $: if (selectedAnalysis === "neighborhood" && selectedRegionCount === 0) {
        selectedAnalysis = null;
        radialPlotData = null;
        selectedCenterBarcode = null;
        availableCenters = [];
    }

    $: if (selectedAnalysis === "neighborhood" && currentSlice === "hbrc") {
        const barcodesKey = selectedBarcodes
            ? selectedBarcodes.length + "-" + (selectedBarcodes[0] || "")
            : "empty";
        const currentKey = `${currentSlice}:${barcodesKey}`;

        if (currentKey !== lastLoadedKey && !loadingCenters) {
            lastLoadedKey = currentKey;
            // Debounce slightly to handle rapid updates from lasso
            setTimeout(() => {
                loadAvailableCenters(
                    selectedBarcodes.length > 0 ? selectedBarcodes : null,
                );
            }, 50);
        }
    }

    // Keep cluster colors consistent with main cluster palette
    function normalizeClusterName(name) {
        return `${name ?? ""}`
            .trim()
            .replace(/^cluster\s+/i, "")
            .replace(/^cluster_/i, "")
            .trim();
    }

    function drawRadialPlot() {
        if (!radialPlotData || !radialPlotData.centers?.length) return;

        const center = radialPlotData.centers[0];
        if (!center || !center.neighbors?.length) return;

        const radialDiv = document.getElementById("radial-plot-container");
        if (!radialDiv) return;
        d3.select(radialDiv).select("svg").remove();

        const width = 600;
        const height = 600;
        const outerRadius = Math.min(width, height) / 2 - 40;
        const innerRadius = outerRadius * 0.35;

        const svg = d3
            .select(radialDiv)
            .append("svg")
            .attr("width", width)
            .attr("height", height);

        const g = svg
            .append("g")
            .attr("transform", `translate(${width / 2}, ${height / 2})`);

        // ---------- 1. 把 neighbors 展开成 gene 级别 ----------
        const geneEntries = [];
        center.neighbors.forEach((n) => {
            const clusterId = String(n.cluster);

            // 后端返回的是 top_genes: [{ gene, score }, ...]，也兼容 n.genes 为 string[]
            const genesArray = Array.isArray(n.top_genes)
                ? n.top_genes.map((g) =>
                      typeof g === "string" ? g : String(g.gene ?? g ?? ""),
                  )
                : Array.isArray(n.genes)
                  ? n.genes.map((g) => String(g))
                  : [];

            genesArray.forEach((gene, geneIdx) => {
                geneEntries.push({
                    gene: String(gene),
                    cluster: clusterId,
                    rank: geneIdx, // 用 rank 控制长度
                    attn: n.attn ?? n.attn_score ?? 0,
                });
            });
        });

        if (!geneEntries.length) return;

        const MAX_GENES = 20;
        const data = geneEntries.slice(0, MAX_GENES);

        // ---------- 2. 角度：每个 gene 一格 ----------
        const angleScale = d3
            .scaleBand()
            .domain(data.map((d) => d.gene))
            .range([0, 2 * Math.PI])
            .padding(0.03); // 控制扇形之间的空隙，越小越“连在一起”

        // ---------- 3. 半径：rank 越靠前越长 ----------
        const maxRank = d3.max(data, (d) => d.rank) || 0;
        const radiusScale = d3
            .scaleLinear()
            .domain([maxRank, 0])
            .range([innerRadius * 1.02, outerRadius]); // 保证和中间空洞有一点间距

        // ---------- 4. 颜色：按 cluster 着色 ----------
        const clusters = [...new Set(data.map((d) => String(d.cluster)))];

        function getColor(d) {
            if (!clusterColorScale || typeof clusterColorScale !== "function") {
                return "#999999";
            }

            const raw = `${d.cluster ?? ""}`;
            const normalized = normalizeClusterName(raw);

            const candidates = [
                normalized,
                raw.trim(),
                `Cluster ${normalized}`,
                `cluster ${normalized}`,
            ].filter((k) => k && k !== "undefined" && k !== "null");

            for (const key of candidates) {
                const c = clusterColorScale(key);
                if (c && c !== "#999999") {
                    return c;
                }
            }

            // 最后兜底再试一次原始值，允许出现灰色 unknown
            return clusterColorScale(raw);
        }

        // ---------- 5. 用 arc 画厚扇形，而不是 line ----------
        const arc = d3
            .arc()
            .innerRadius(innerRadius)
            .outerRadius((d) => radiusScale(d.rank))
            .startAngle((d) => angleScale(d.gene))
            .endAngle((d) => angleScale(d.gene) + angleScale.bandwidth());

        const bars = g
            .selectAll(".gene-sector")
            .data(data)
            .enter()
            .append("g")
            .attr("class", "gene-sector");

        bars.append("path")
            .attr("d", arc)
            .attr("fill", (d) => getColor(d))
            .attr("stroke", "#ffffff")
            .attr("stroke-width", 1);

        // ---------- 6. 外圈 gene 标签 ----------
        bars.append("text")
            .attr("x", (d) => {
                const a = angleScale(d.gene) + angleScale.bandwidth() / 2;
                const r = outerRadius + 18;
                return Math.cos(a) * r;
            })
            .attr("y", (d) => {
                const a = angleScale(d.gene) + angleScale.bandwidth() / 2;
                const r = outerRadius + 18;
                return Math.sin(a) * r;
            })
            .attr("text-anchor", "middle")
            .attr("alignment-baseline", "middle")
            .attr("font-size", "11px")
            .attr("fill", "#333")
            .text((d) => d.gene);

        // ---------- 7. 中心文案 ----------
        g.append("text")
            .attr("text-anchor", "middle")
            .attr("dy", "-0.2em")
            .attr("font-size", "16px")
            .attr("font-weight", "bold")
            .text(
                center.center_index ??
                    center.center_barcode.substring(0, 12) + "...",
            );

        if (center.center_cluster_label) {
            g.append("text")
                .attr("text-anchor", "middle")
                .attr("dy", "1.2em")
                .attr("font-size", "12px")
                .attr("fill", "#777")
                .text(center.center_cluster_label);
        }

        // ---------- 8. 背景同心圆 ----------
        const ringSteps = [0.25, 0.5, 0.75, 1.0];
        g.selectAll(".radius-ring")
            .data(ringSteps)
            .enter()
            .append("circle")
            .attr("class", "radius-ring")
            .attr("r", (f) => innerRadius + (outerRadius - innerRadius) * f)
            .attr("fill", "none")
            .attr("stroke", "#e5e5e5")
            .attr("stroke-dasharray", "2,2")
            .attr("stroke-width", 0.8);
    }

    // SVG Analysis reactive statements
    $: if (
        selectedAnalysis === "svg" &&
        selectedSVGCluster &&
        hvg &&
        hvg[selectedSVGCluster] &&
        barChartDiv &&
        currentGeneMode === "Bar"
    ) {
        tick().then(() => {
            drawEnrichmentChart(hvg[selectedSVGCluster]);
        });
    }

    $: if (
        selectedAnalysis === "svg" &&
        selectedSVGCluster &&
        hvg &&
        hvg[selectedSVGCluster] &&
        sankeyDiv &&
        currentGeneMode !== "Bar"
    ) {
        tick().then(() => {
            const topTermCount = 10;
            const topGenePerTerm = 5;

            let data = hvg[selectedSVGCluster];
            const topTerms = [...data]
                .sort((a, b) => a["Adjusted P-value"] - b["Adjusted P-value"])
                .slice(0, topTermCount);

            const filteredResults = topTerms.map((term) => {
                const geneList = term.Genes.split(";").map((g) => g.trim());
                const topGenes = geneList.slice(0, topGenePerTerm);
                return {
                    ...term,
                    Genes: topGenes.join(";"),
                };
            });

            drawSankey(filteredResults);
        });
    }

    // When SVG cluster changes, load data for new cluster if not already loaded
    $: if (
        selectedAnalysis === "svg" &&
        selectedSVGCluster &&
        !hvg[selectedSVGCluster] &&
        selectedSVGCluster !== currentCluster
    ) {
        loadSVGAnalysis(selectedSVGCluster);
    }

    // Spatial Communication reactive statements
    $: if (
        selectedAnalysis === "spatial" &&
        cellChat &&
        cellChatDivNumber &&
        cellChatDivStrength
    ) {
        tick().then(() => {
            if (currentInteractionMode === "Cluster") {
                drawClusterChatNumber();
                drawClusterChatStrength();
            } else {
                drawLigandReceptorChordNumber();
                drawLigandReceptorChordStrength();
            }
        });
    }

    async function drawEnrichmentChart(results) {
        if (!Array.isArray(results)) {
            return;
        }

        // Wait for DOM to be ready before getting container dimensions
        await tick();

        const clusters = [...new Set(results.map((d) => d.Category))];
        const colorScale = d3.scaleOrdinal(d3.schemeTableau10).domain(clusters);

        const maxLabelLength = 60;
        const yLabels = [...results].map((d) =>
            d.Term.length > maxLabelLength
                ? d.Term.slice(0, maxLabelLength - 3) + "..."
                : d.Term,
        );

        const tracesMap = new Map();

        for (const d of results) {
            const label =
                d.Term.length > maxLabelLength
                    ? d.Term.slice(0, maxLabelLength - 3) + "..."
                    : d.Term;

            if (!tracesMap.has(d.Category)) {
                tracesMap.set(d.Category, {
                    type: "bar",
                    name: d.Category,
                    x: [],
                    y: [],
                    orientation: "h",
                    text: [],
                    textposition: "none",
                    customdata: [],
                    hovertemplate:
                        "<b>%{text}</b><br>Category: " +
                        d.Category +
                        "<br>-log10(p-adj): %{x:.2f}<br>Genes: %{customdata}<extra></extra>",
                    marker: {
                        color: colorScale(d.Category),
                    },
                });
            }

            const trace = tracesMap.get(d.Category);
            trace.x.push(-Math.log10(d["Adjusted P-value"]));
            trace.y.push(label);
            trace.text.push(d.Term);
            trace.customdata.push(d.Genes.split(";").length);
        }

        const traces = [...tracesMap.values()];

        // Get container dimensions for responsive sizing
        const containerHeight =
            barChartDiv?.clientHeight || yLabels.length * 20;
        const containerWidth = barChartDiv?.clientWidth || undefined;

        const layout = {
            showlegend: true,
            title: "Top Enriched Terms (Grouped by Category)",
            barmode: "group",
            margin: { l: 200, r: 0, t: 100, b: 20 },
            autosize: true,
            height: containerHeight,
            width: containerWidth,
            yaxis: {
                categoryorder: "array",
                categoryarray: yLabels,
                automargin: true,
                tickfont: { size: 12 },
            },
            xaxis: {
                title: "-log10(Adjusted P-value)",
                tickfont: { size: 12 },
            },
            legend: {
                // Lay out legend items horizontally, starting slightly left of the y-axis tick labels
                // Use a small negative x so the first legend item aligns with the left edge of y-axis text
                orientation: "h",
                x: -0.1,
                y: 1.1,
                xanchor: "left",
                yanchor: "bottom",
            },
        };

        Plotly.newPlot(barChartDiv, traces, layout, {
            scrollZoom: false,
            responsive: true,
            useResizeHandler: true,
            displaylogo: false,
            modeBarButtons: [["pan2d", "resetScale2d", "toImage"]],
        });

        observeResize(barChartDiv, () => {
            safePlotResize(barChartDiv);
        });
        window.addEventListener("resize", () => {
            safePlotResize(barChartDiv);
        });
    }

    async function drawSankey(results) {
        // Wait for DOM to be ready before getting container dimensions
        await tick();

        const geneSet = new Set();
        const termSet = [];
        const links = [];

        results.forEach((item) => {
            const genes = item.Genes.split(";");
            const term = item.Term;
            termSet.push(term);
            genes.forEach((g) => {
                geneSet.add(g);
                links.push({ source: g, target: term });
            });
        });

        const genes = [...geneSet];
        const terms = [...new Set(termSet)];
        const nodes = genes.concat(terms);
        const nodeIndex = Object.fromEntries(nodes.map((n, i) => [n, i]));

        const pastelColors = [
            "#aec6cf",
            "#ffb347",
            "#77dd77",
            "#f49ac2",
            "#cfcfc4",
            "#b39eb5",
            "#ff6961",
            "#cb99c9",
            "#fdfd96",
            "#836953",
        ];

        const lightColors = [
            "#b3cde0",
            "#decbe4",
            "#fed9a6",
            "#ccebc5",
            "#fbb4ae",
            "#e5d8bd",
            "#f2f2f2",
            "#d9d9d9",
            "#e6f5c9",
            "#fddaec",
        ];
        const geneColorScale = d3.scaleOrdinal(pastelColors);
        const termColorScale = d3.scaleOrdinal(lightColors);

        const nodeColors = nodes.map((n, i) =>
            i < genes.length ? geneColorScale(n) : termColorScale(n),
        );

        const nodeColorMap = Object.fromEntries(
            nodes.map((n, i) => [n, nodeColors[i]]),
        );

        const linkColors = links.map((l) => nodeColorMap[l.source]);

        function hexToRgb(hex) {
            hex = hex.replace("#", "");
            const bigint = parseInt(hex, 16);
            const r = (bigint >> 16) & 255;
            const g = (bigint >> 8) & 255;
            const b = bigint & 255;
            return [r, g, b];
        }

        const data = {
            type: "sankey",
            orientation: "h",
            node: {
                pad: 6,
                thickness: 20,
                line: { color: "black", width: 0.5 },
                label: nodes,
                color: nodeColors,
            },
            link: {
                source: links.map((l) => nodeIndex[l.source]),
                target: links.map((l) => nodeIndex[l.target]),
                value: links.map(() => 1),
                color: links.map((l) => {
                    const hex = geneColorScale(l.source);
                    const [r, g, b] = hexToRgb(hex);
                    return `rgba(${r}, ${g}, ${b}, 0.5)`;
                }),
            },
        };

        // Get container dimensions for responsive sizing
        const containerHeight =
            sankeyDiv?.clientHeight || Math.max(400, nodes.length * 20);
        const containerWidth = sankeyDiv?.clientWidth || undefined;

        const layout = {
            title: "Sankey: Genes → Enriched Terms",
            font: { size: 10 },
            margin: { l: 20, r: 20, t: 40, b: 10 },
            autosize: true,
            height: containerHeight,
            width: containerWidth,
        };

        Plotly.newPlot(sankeyDiv, [data], layout, {
            displaylogo: false,
            responsive: true,
        });

        observeResize(sankeyDiv, () => {
            safePlotResize(sankeyDiv);
        });
        window.addEventListener("resize", () => {
            safePlotResize(sankeyDiv);
        });
    }

    function drawClusterChatNumber() {
        if (!cellChat || !cellChat.number_matrix) return;
        d3.select(cellChatDivNumber).select("svg").remove();
        const width = 400;
        const height = 420; // Increase height to accommodate text labels
        const innerRadius = Math.min(width, height) * 0.5 - 20;
        const outerRadius = innerRadius + 6;
        let names = cellChat.cluster_names;
        let matrix = cellChat.number_matrix.map((row) => [...row]);
        matrix = getTopNMatrix(matrix, 10);

        // Filter out clusters with no communication
        const filtered = filterEmptyClusters(matrix, names);
        matrix = filtered.matrix;
        names = filtered.names;

        if (matrix.length === 0 || names.length === 0) {
            return; // No clusters with communication
        }

        const n = names.length;
        const groupScores = matrix.map(
            (row, i) =>
                row.reduce((a, b) => a + b, 0) +
                matrix.reduce((a, b) => a + b[i], 0),
        );
        const chord = d3
            .chordDirected()
            .padAngle(12 / innerRadius)
            .sortSubgroups(d3.descending)
            .sortChords(d3.descending);
        const chords = chord(matrix);
        chords.groups.forEach((d) => {
            d.name = names[d.index];
        });
        chords.forEach((d) => {
            d.source.name = names[d.source.index];
            d.target.name = names[d.target.index];
        });
        const radiusScale = d3
            .scaleSqrt()
            .domain([0, d3.max(groupScores)])
            .range([innerRadius, innerRadius + 15]);
        const arc = d3
            .arc()
            .innerRadius(innerRadius)
            .outerRadius((d) => radiusScale(groupScores[d.index]));
        const ribbon = d3
            .ribbonArrow()
            .radius(innerRadius - 0.5)
            .padAngle(1 / innerRadius);
        // Add padding to viewBox to accommodate text labels that may extend beyond the circle
        const padding = 30;
        const svg = d3
            .select(cellChatDivNumber)
            .append("svg")
            .attr("width", width)
            .attr("height", height)
            .attr("viewBox", [
                -(width / 2 + padding),
                -(height / 2 + padding),
                width + 2 * padding,
                height + 2 * padding,
            ])
            .attr(
                "style",
                "width: 100%; height: 100%; font: 10px sans-serif; overflow: visible;",
            );
        const textId = "text-path-id-number";
        svg.append("path")
            .attr("id", textId)
            .attr("fill", "none")
            .attr(
                "d",
                d3.arc()({ outerRadius, startAngle: 0, endAngle: 2 * Math.PI }),
            );
        svg.append("g")
            .attr("fill-opacity", 0.75)
            .selectAll("path")
            .data(chords)
            .join("path")
            .attr("d", ribbon)
            .attr("fill", (d) => clusterColorScale(d.source.name))
            .style("mix-blend-mode", "multiply")
            .append("title")
            .text((d) => {
                const sourceName = names[d.source.index];
                const targetName = names[d.target.index];
                const value = matrix[d.source.index][d.target.index];
                return `Cluster: ${sourceName} > ${targetName}\nValue: ${value}`;
            });
        const g = svg.append("g").selectAll("g").data(chords.groups).join("g");
        g.append("path")
            .attr("d", arc)
            .attr("fill", (d) => clusterColorScale(d.name))
            .attr("stroke", "#fff");
        g.append("text")
            .attr("dy", -3)
            .append("textPath")
            .attr("xlink:href", `#${textId}`)
            .attr("startOffset", (d) => d.startAngle * outerRadius)
            .text((d) => names[d.index]);
        g.append("title").text((d) => {
            const out = d3.sum(matrix[d.index]);
            const _in = d3.sum(matrix, (row) => row[d.index]);
            return `${names[d.index]}\n→ others: ${out}\n← from others: ${_in}`;
        });
    }

    function drawClusterChatStrength() {
        if (!cellChat || !cellChat.strength_matrix) return;
        d3.select(cellChatDivStrength).select("svg").remove();
        const width = 400;
        const height = 420; // Increase height to accommodate text labels
        const innerRadius = Math.min(width, height) * 0.5 - 20;
        const outerRadius = innerRadius + 6;
        let names = cellChat.cluster_names;
        let matrix = cellChat.strength_matrix.map((row) => [...row]);
        matrix = getTopNMatrix(matrix, 10);

        // Filter out clusters with no communication
        const filtered = filterEmptyClusters(matrix, names);
        matrix = filtered.matrix;
        names = filtered.names;

        if (matrix.length === 0 || names.length === 0) {
            return; // No clusters with communication
        }

        const n = names.length;
        const groupScores = matrix.map(
            (row, i) =>
                row.reduce((a, b) => a + b, 0) +
                matrix.reduce((a, b) => a + b[i], 0),
        );
        const chord = d3
            .chordDirected()
            .padAngle(12 / innerRadius)
            .sortSubgroups(d3.descending)
            .sortChords(d3.descending);
        const chords = chord(matrix);
        chords.groups.forEach((d) => {
            d.name = names[d.index];
        });
        chords.forEach((d) => {
            d.source.name = names[d.source.index];
            d.target.name = names[d.target.index];
        });
        const radiusScale = d3
            .scaleSqrt()
            .domain([0, d3.max(groupScores)])
            .range([innerRadius, innerRadius + 15]);
        const arc = d3
            .arc()
            .innerRadius(innerRadius)
            .outerRadius((d) => radiusScale(groupScores[d.index]));
        const ribbon = d3
            .ribbonArrow()
            .radius(innerRadius - 0.5)
            .padAngle(1 / innerRadius);
        // Add padding to viewBox to accommodate text labels that may extend beyond the circle
        const padding = 30;
        const svg = d3
            .select(cellChatDivStrength)
            .append("svg")
            .attr("width", width)
            .attr("height", height)
            .attr("viewBox", [
                -(width / 2 + padding),
                -(height / 2 + padding),
                width + 2 * padding,
                height + 2 * padding,
            ])
            .attr(
                "style",
                "width: 100%; height: 100%; font: 10px sans-serif; overflow: visible;",
            );
        const textId = "text-path-id-strength";
        svg.append("path")
            .attr("id", textId)
            .attr("fill", "none")
            .attr(
                "d",
                d3.arc()({ outerRadius, startAngle: 0, endAngle: 2 * Math.PI }),
            );
        svg.append("g")
            .attr("fill-opacity", 0.75)
            .selectAll("path")
            .data(chords)
            .join("path")
            .attr("d", ribbon)
            .attr("fill", (d) => clusterColorScale(d.source.name))
            .style("mix-blend-mode", "multiply")
            .append("title")
            .text((d) => {
                const sourceName = names[d.source.index];
                const targetName = names[d.target.index];
                const value = matrix[d.source.index][d.target.index];
                return `Cluster: ${sourceName} > ${targetName}\nValue: ${value.toFixed(3)}`;
            });
        const g = svg.append("g").selectAll("g").data(chords.groups).join("g");
        g.append("path")
            .attr("d", arc)
            .attr("fill", (d) => clusterColorScale(d.name))
            .attr("stroke", "#fff");
        g.append("text")
            .attr("dy", -3)
            .append("textPath")
            .attr("xlink:href", `#${textId}`)
            .attr("startOffset", (d) => d.startAngle * outerRadius)
            .text((d) => names[d.index]);
        g.append("title").text((d) => {
            const out = d3.sum(matrix[d.index]);
            const _in = d3.sum(matrix, (row) => row[d.index]);
            return `${names[d.index]}\n→ others: ${out.toFixed(3)}\n← from others: ${_in.toFixed(3)}`;
        });
    }

    function drawLigandReceptorChordNumber() {
        if (!cellChat || !cellChat.top_interactions) return;
        d3.select(ligandReceptorDivNumber).select("svg").remove();

        const topInteractions = cellChat.top_interactions.slice(0, 10);

        if (topInteractions.length === 0) {
            console.warn("没有配体-受体相互作用数据");
            return;
        }

        const ligands = [...new Set(topInteractions.map((d) => d.ligand))];
        const receptors = [...new Set(topInteractions.map((d) => d.receptor))];
        const allNodes = [...ligands, ...receptors];

        const n = allNodes.length;
        const matrix = Array.from({ length: n }, () => Array(n).fill(0));

        topInteractions.forEach((interaction) => {
            const ligandIdx = allNodes.indexOf(interaction.ligand);
            const receptorIdx = allNodes.indexOf(interaction.receptor);
            if (ligandIdx !== -1 && receptorIdx !== -1) {
                matrix[ligandIdx][receptorIdx] = 1;
            }
        });

        const width = 500;
        const height = 520; // Increase height to accommodate text labels
        const innerRadius = Math.min(width, height) * 0.5 - 20;
        const outerRadius = innerRadius + 6;

        const chord = d3
            .chordDirected()
            .padAngle(12 / innerRadius)
            .sortSubgroups(d3.descending)
            .sortChords(d3.descending);
        const chords = chord(matrix);

        chords.groups.forEach((d) => {
            d.name = allNodes[d.index];
        });
        chords.forEach((d) => {
            d.source.name = allNodes[d.source.index];
            d.target.name = allNodes[d.target.index];
        });

        const groupScores = matrix.map(
            (row, i) =>
                row.reduce((a, b) => a + b, 0) +
                matrix.reduce((a, b) => a + b[i], 0),
        );
        const radiusScale = d3
            .scaleSqrt()
            .domain([0, d3.max(groupScores)])
            .range([innerRadius, innerRadius + 15]);
        const arc = d3
            .arc()
            .innerRadius(innerRadius)
            .outerRadius((d) => radiusScale(groupScores[d.index]));
        const ribbon = d3
            .ribbonArrow()
            .radius(innerRadius - 0.5)
            .padAngle(1 / innerRadius);

        // Add padding to viewBox to accommodate text labels that may extend beyond the circle
        const padding = 30;
        const svg = d3
            .select(ligandReceptorDivNumber)
            .append("svg")
            .attr("width", width)
            .attr("height", height)
            .attr("viewBox", [
                -(width / 2 + padding),
                -(height / 2 + padding),
                width + 2 * padding,
                height + 2 * padding,
            ])
            .attr(
                "style",
                "width: 100%; height: 100%; font: 10px sans-serif; overflow: visible;",
            );

        const textId = "text-path-id-lr";
        svg.append("path")
            .attr("id", textId)
            .attr("fill", "none")
            .attr(
                "d",
                d3.arc()({ outerRadius, startAngle: 0, endAngle: 2 * Math.PI }),
            );

        const colorScale = d3
            .scaleOrdinal()
            .domain(allNodes)
            .range(
                allNodes.map((node) => {
                    if (ligands.includes(node)) {
                        return d3.schemeBlues[9][6];
                    } else {
                        return d3.schemeReds[9][6];
                    }
                }),
            );

        svg.append("g")
            .attr("fill-opacity", 0.75)
            .selectAll("path")
            .data(chords)
            .join("path")
            .attr("d", ribbon)
            .attr("fill", (d) => colorScale(allNodes[d.source.index]))
            .style("mix-blend-mode", "multiply")
            .append("title")
            .text((d) => {
                const sourceName = allNodes[d.source.index];
                const targetName = allNodes[d.target.index];
                const value = matrix[d.source.index][d.target.index];
                return `${sourceName} → ${targetName}\nCount: ${value.toFixed(0)}`;
            });

        const g = svg.append("g").selectAll("g").data(chords.groups).join("g");
        g.append("path")
            .attr("d", arc)
            .attr("fill", (d) => colorScale(allNodes[d.index]))
            .attr("stroke", "#fff");
        g.append("text")
            .attr("dy", -3)
            .append("textPath")
            .attr("xlink:href", `#${textId}`)
            .attr("startOffset", (d) => d.startAngle * outerRadius)
            .text((d) => allNodes[d.index]);
        g.append("title").text((d) => {
            const out = d3.sum(matrix[d.index]);
            const _in = d3.sum(matrix, (row) => row[d.index]);
            return `${allNodes[d.index]}\n→ others: ${out.toFixed(0)}\n← from others: ${_in.toFixed(0)}`;
        });
    }

    function drawLigandReceptorChordStrength() {
        if (!cellChat || !cellChat.top_interactions) return;
        d3.select(ligandReceptorDivStrength).select("svg").remove();

        const topInteractions = cellChat.top_interactions.slice(0, 10);

        if (topInteractions.length === 0) {
            console.warn("没有配体-受体相互作用数据");
            return;
        }

        const ligands = [...new Set(topInteractions.map((d) => d.ligand))];
        const receptors = [...new Set(topInteractions.map((d) => d.receptor))];
        const allNodes = [...ligands, ...receptors];

        const n = allNodes.length;
        const matrix = Array.from({ length: n }, () => Array(n).fill(0));

        topInteractions.forEach((interaction) => {
            const ligandIdx = allNodes.indexOf(interaction.ligand);
            const receptorIdx = allNodes.indexOf(interaction.receptor);
            if (ligandIdx !== -1 && receptorIdx !== -1) {
                matrix[ligandIdx][receptorIdx] = interaction.interaction_score;
            }
        });

        const width = 500;
        const height = 520; // Increase height to accommodate text labels
        const innerRadius = Math.min(width, height) * 0.5 - 20;
        const outerRadius = innerRadius + 6;

        const chord = d3
            .chordDirected()
            .padAngle(12 / innerRadius)
            .sortSubgroups(d3.descending)
            .sortChords(d3.descending);
        const chords = chord(matrix);

        chords.groups.forEach((d) => {
            d.name = allNodes[d.index];
        });
        chords.forEach((d) => {
            d.source.name = allNodes[d.source.index];
            d.target.name = allNodes[d.target.index];
        });

        const groupScores = matrix.map(
            (row, i) =>
                row.reduce((a, b) => a + b, 0) +
                matrix.reduce((a, b) => a + b[i], 0),
        );
        const radiusScale = d3
            .scaleSqrt()
            .domain([0, d3.max(groupScores)])
            .range([innerRadius, innerRadius + 15]);
        const arc = d3
            .arc()
            .innerRadius(innerRadius)
            .outerRadius((d) => radiusScale(groupScores[d.index]));
        const ribbon = d3
            .ribbonArrow()
            .radius(innerRadius - 0.5)
            .padAngle(1 / innerRadius);

        // Add padding to viewBox to accommodate text labels that may extend beyond the circle
        const padding = 30;
        const svg = d3
            .select(ligandReceptorDivStrength)
            .append("svg")
            .attr("width", width)
            .attr("height", height)
            .attr("viewBox", [
                -(width / 2 + padding),
                -(height / 2 + padding),
                width + 2 * padding,
                height + 2 * padding,
            ])
            .attr(
                "style",
                "width: 100%; height: 100%; font: 10px sans-serif; overflow: visible;",
            );

        const textId = "text-path-id-lr-strength";
        svg.append("path")
            .attr("id", textId)
            .attr("fill", "none")
            .attr(
                "d",
                d3.arc()({ outerRadius, startAngle: 0, endAngle: 2 * Math.PI }),
            );

        const colorScale = d3
            .scaleOrdinal()
            .domain(allNodes)
            .range(
                allNodes.map((node) => {
                    if (ligands.includes(node)) {
                        return d3.schemeBlues[9][6];
                    } else {
                        return d3.schemeReds[9][6];
                    }
                }),
            );

        svg.append("g")
            .attr("fill-opacity", 0.75)
            .selectAll("path")
            .data(chords)
            .join("path")
            .attr("d", ribbon)
            .attr("fill", (d) => colorScale(allNodes[d.source.index]))
            .style("mix-blend-mode", "multiply")
            .append("title")
            .text((d) => {
                const sourceName = allNodes[d.source.index];
                const targetName = allNodes[d.target.index];
                const value = matrix[d.source.index][d.target.index];
                return `${sourceName} → ${targetName}\nStrength: ${value.toFixed(3)}`;
            });

        const g = svg.append("g").selectAll("g").data(chords.groups).join("g");
        g.append("path")
            .attr("d", arc)
            .attr("fill", (d) => colorScale(allNodes[d.index]))
            .attr("stroke", "#fff");
        g.append("text")
            .attr("dy", -3)
            .append("textPath")
            .attr("xlink:href", `#${textId}`)
            .attr("startOffset", (d) => d.startAngle * outerRadius)
            .text((d) => allNodes[d.index]);
        g.append("title").text((d) => {
            const out = d3.sum(matrix[d.index]);
            const _in = d3.sum(matrix, (row) => row[d.index]);
            return `${allNodes[d.index]}\n→ others: ${out.toFixed(3)}\n← from others: ${_in.toFixed(3)}`;
        });
    }

    // DEG Analysis functions
    async function loadHeatmap() {
        if (!clusterGeneExpression || !clusterGeneExpressionDiv) return;

        // Wait for DOM to be ready before getting container dimensions
        await tick();

        const genes = clusterGeneExpression.genes;
        const barcodes = clusterGeneExpression.barcodes;
        const clusters = clusterGeneExpression.clusters;
        const expression = clusterGeneExpression.expression;

        // 计算 cluster 分段 span
        const clusterSpans = [];
        let prev = clusters[0];
        let start = 0;
        for (let i = 1; i <= clusters.length; i++) {
            if (clusters[i] !== prev) {
                clusterSpans.push({ cluster: prev, start, end: i - 1 });
                prev = clusters[i];
                start = i;
            }
        }

        // 构造 customdata
        const customdata = genes.map((gene, i) =>
            barcodes.map((barcode, j) => ({
                barcode,
                cluster: clusters[j],
                gene,
                expression: expression[i][j],
            })),
        );

        // heatmap trace
        const heatmap = {
            z: expression,
            x: barcodes,
            y: genes,
            type: "heatmap",
            colorscale: "RdBu",
            zmin: -2,
            zmax: 2,
            showscale: true,
            xgap: 0,
            ygap: 0,
            customdata,
            hovertemplate:
                "Barcode: %{customdata.barcode}<br>" +
                "Cluster: %{customdata.cluster}<br>" +
                "Gene: %{customdata.gene}<br>" +
                "Expression: %{z:.2f}<extra></extra>",
            colorbar: {
                orientation: "h",
                x: 0.5,
                y: -0.1,
                xanchor: "center",
                yanchor: "top",
                len: 0.6,
                thickness: 20,
                title: "Expression",
            },
        };

        // cluster color bar
        const shapes = clusterSpans.map((span) => ({
            type: "rect",
            xref: "x",
            yref: "paper",
            x0: barcodes[span.start],
            x1: barcodes[span.end],
            y0: 1.01,
            y1: 1.05,
            fillcolor: clusterColorScale(span.cluster),
            line: { width: 0 },
        }));

        // cluster分界线
        const vlines = clusterSpans.slice(1).map((span) => ({
            type: "line",
            xref: "x",
            yref: "paper",
            x0: barcodes[span.start],
            x1: barcodes[span.start],
            y0: 0,
            y1: 1,
            line: { color: "#888", width: 2 },
        }));

        const layout = {
            title: "Single-Cell/Spot DEG Heatmap",
            margin: { t: 60, l: 80, r: 10, b: 10 },
            // Let Plotly autosize to fill the container (wrapper div is flex-1 + h-full)
            autosize: true,
            height: undefined,
            width: undefined,
            xaxis: {
                tickangle: 90,
                side: "bottom",
                showticklabels: false,
            },
            yaxis: {
                autorange: "reversed",
            },
            shapes: [...shapes, ...vlines],
            showlegend: false,
        };

        Plotly.newPlot(clusterGeneExpressionDiv, [heatmap], layout, {
            responsive: true,
            displayModeBar: false,
        });

        observeResize(clusterGeneExpressionDiv, () => {
            safePlotResize(clusterGeneExpressionDiv);
        });
    }

    async function drawDotPlot() {
        if (!clusterGeneDot || !clusterGeneDotDiv) return;

        // Wait for DOM to be ready before getting container dimensions
        await tick();

        const { genes, clusters, data } = clusterGeneDot;

        // 构建矩阵
        const avgMatrix = genes.map((g) =>
            clusters.map((cl) => {
                const entry = data.find(
                    (d) => d.gene === g && d.cluster === cl,
                );
                return entry ? entry.avg_expr : 0;
            }),
        );
        const pctMatrix = genes.map((g) =>
            clusters.map((cl) => {
                const entry = data.find(
                    (d) => d.gene === g && d.cluster === cl,
                );
                return entry ? entry.pct_expr * 100 : 0;
            }),
        );

        // 展平成一维
        const x = [];
        const y = [];
        const avgFlat = [];
        const pctFlat = [];
        for (let i = 0; i < genes.length; i++) {
            for (let j = 0; j < clusters.length; j++) {
                x.push(clusters[j]);
                y.push(genes[i]);
                avgFlat.push(avgMatrix[i][j]);
                pctFlat.push(pctMatrix[i][j]);
            }
        }

        const layout = {
            title: "Dotplot: Gene Expression per Cluster",
            xaxis: {
                title: "Cluster",
                tickvals: clusters,
                ticktext: clusters,
                automargin: false,
                showgrid: false,
                zeroline: false,
                showline: false,
            },
            yaxis: {
                title: "",
                tickvals: genes,
                ticktext: genes,
                automargin: false,
                autorange: "reversed",
                range: [-0.499, genes.length - 0.501],
                showgrid: true,
                gridcolor: "#eee",
                gridwidth: 1,
            },
            // Let Plotly autosize to fill the container
            autosize: true,
            height: undefined,
            width: undefined,
            margin: { t: 0, b: 20, l: 80, r: 10 },
            showlegend: false,
        };

        const trace = {
            type: "scatter",
            mode: "markers",
            x: x,
            y: y,
            marker: {
                size: pctFlat,
                sizemode: "area",
                sizeref: (2.0 * Math.max(...pctFlat)) / 20 ** 2,
                sizemin: 2,
                color: avgFlat,
                colorscale: "PuBuGn",
                colorbar: {
                    title: "Average Expression",
                    x: 1.02,
                    y: 0.5,
                    xanchor: "left",
                    yanchor: "middle",
                    len: 0.6,
                    thickness: 20,
                },
                line: { width: 0 },
            },
            hovertemplate:
                "<b>%{y}</b> – %{x}<br>Cluster: %{x}<br>Avg Expr: %{marker.color:.2f}<br>Percent: %{marker.size:.1f}%<extra></extra>",
            showlegend: false,
        };

        Plotly.newPlot(clusterGeneDotDiv, [trace], layout, {
            responsive: true,
            useResizeHandler: true,
            displayModeBar: false,
        });

        observeResize(clusterGeneDotDiv, () => {
            safePlotResize(clusterGeneDotDiv);
        });
    }

    // DEG Analysis reactive statements
    $: if (
        selectedAnalysis === "deg" &&
        clusterGeneExpression &&
        clusterGeneExpressionDiv &&
        currentDEGMode === "Heatmap"
    ) {
        tick().then(() => {
            loadHeatmap();
        });
    }

    $: if (
        selectedAnalysis === "deg" &&
        clusterGeneDot &&
        clusterGeneDotDiv &&
        currentDEGMode === "Dotplot"
    ) {
        tick().then(() => {
            drawDotPlot();
        });
    }

    // Helper function to convert image to base64
    function toBase64(img) {
        const canvas = document.createElement("canvas");
        canvas.width = img.width;
        canvas.height = img.height;
        const ctx = canvas.getContext("2d");
        ctx.drawImage(img, 0, 0);
        return canvas.toDataURL("image/png");
    }

    // Convert Plotly coordinates to pixel coordinates
    // This follows the same logic as clusterResultComparison
    function plotlyToPixelCoords(plotlyX, plotlyY, plotDiv) {
        if (!plotDiv || !plotDiv._fullLayout) return null;

        try {
            const layout = plotDiv._fullLayout;

            if (!layout || !layout.xaxis || !layout.yaxis) return null;

            // Get the plot div's bounding rect (relative to viewport)
            const rect = plotDiv.getBoundingClientRect();

            // Get axis ranges - use _rl (range linear) which is the actual displayed range
            const xaxis = layout.xaxis;
            const yaxis = layout.yaxis;
            const xRange = xaxis._rl || xaxis.range || [0, 1];
            const yRange = yaxis._rl || yaxis.range || [0, 1];

            // Get plot area margins to understand where the actual plot area is
            const plotArea = layout._size || {};
            const plotLeft = plotArea.l || 0;
            const plotTop = plotArea.t || 0;

            // Calculate plot area dimensions
            const plotAreaWidth =
                rect.width - (plotArea.l || 0) - (plotArea.r || 0);
            const plotAreaHeight =
                rect.height - (plotArea.t || 0) - (plotArea.b || 0);

            // Calculate scale factors
            const xRangeSize = xRange[1] - xRange[0];
            const yRangeSize = yRange[1] - yRange[0];

            if (xRangeSize === 0 || yRangeSize === 0) {
                console.warn("Invalid axis range size:", { xRange, yRange });
                return null;
            }

            // Convert data coordinates to plot area coordinates, then add margins
            const xInPlotArea =
                ((plotlyX - xRange[0]) / xRangeSize) * plotAreaWidth;
            const yInPlotArea =
                ((plotlyY - yRange[0]) / yRangeSize) * plotAreaHeight;

            // For reversed y-axis (yRange[0] > yRange[1]), we need to flip
            const pixelX = plotLeft + xInPlotArea;
            const pixelY = plotTop + (plotAreaHeight - yInPlotArea); // Flip for reversed y-axis

            return { x: pixelX, y: pixelY };
        } catch (err) {
            console.error(
                "Error converting coordinates:",
                err,
                plotlyX,
                plotlyY,
            );
            return null;
        }
    }

    // Draw pie charts on canvas overlay
    function drawDeconvolutionPieCharts() {
        if (
            !deconvolutionPieCanvas ||
            !deconvolutionDiv ||
            !deconvolutionData ||
            !spatialData
        ) {
            return;
        }

        const cellTypes = deconvolutionData.cell_types || [];
        const spots = deconvolutionData.spots || [];
        const proportions = deconvolutionData.proportions || [];

        if (cellTypes.length === 0 || spots.length === 0) {
            return;
        }

        // Build a map from barcode to spatial coordinates
        const barcodeToCoords = new Map();
        spatialData.forEach((trace) => {
            const barcodes = trace.customdata || trace.text || [];
            const xs = trace.x || [];
            const ys = trace.y || [];
            barcodes.forEach((barcode, idx) => {
                if (barcode && idx < xs.length && idx < ys.length) {
                    barcodeToCoords.set(String(barcode).trim(), {
                        x: xs[idx],
                        y: ys[idx],
                    });
                }
            });
        });

        // Setup canvas
        const rect = deconvolutionPieCanvas.getBoundingClientRect();
        const dpr =
            typeof window !== "undefined" ? window.devicePixelRatio || 1 : 1;
        const physicalWidth = Math.floor(rect.width * dpr);
        const physicalHeight = Math.floor(rect.height * dpr);

        if (
            deconvolutionPieCanvas.width !== physicalWidth ||
            deconvolutionPieCanvas.height !== physicalHeight
        ) {
            deconvolutionPieCanvas.width = physicalWidth;
            deconvolutionPieCanvas.height = physicalHeight;
        }
        deconvolutionPieCanvas.style.width = `${rect.width}px`;
        deconvolutionPieCanvas.style.height = `${rect.height}px`;

        const ctx = deconvolutionPieCanvas.getContext("2d");
        if (!ctx) return;

        ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.clearRect(0, 0, physicalWidth, physicalHeight);
        ctx.scale(dpr, dpr);

        // Disable stroke
        ctx.lineWidth = 0;
        ctx.strokeStyle = "transparent";

        // Color palette for cell types - use different colors from cluster colors
        const colorPalette = [
            "#4e79a7",
            "#f28e2b",
            "#e15759",
            "#76b7b2",
            "#59a14f",
            "#edc948",
            "#b07aa1",
            "#ff9da7",
            "#9c755f",
            "#bab0ab",
        ];
        const getColor = (index) => colorPalette[index % colorPalette.length];

        // Pie chart radius - match spot size (spot size is typically 4 in Plotly, so radius should be ~2)
        // In comparison view, radius is 2.5, which matches spot size of ~5
        // For consistency, use radius of 2 to match spot size of 4
        const radius = 3;

        // Draw pie chart for each spot
        let drawnCount = 0;
        spots.forEach((barcode, spotIdx) => {
            const coords = barcodeToCoords.get(String(barcode).trim());
            if (!coords) {
                if (spotIdx < 5)
                    console.warn("No coordinates for barcode:", barcode);
                return;
            }

            const spotProportions = proportions[spotIdx] || [];
            const pieSlices = cellTypes
                .map((cellType, typeIdx) => {
                    const color = getColor(typeIdx);
                    return {
                        cellType,
                        value: spotProportions[typeIdx] || 0,
                        color: color,
                    };
                })
                .filter((slice) => slice.value > 0.01); // Only show slices > 1%

            if (pieSlices.length === 0) return;

            // Convert Plotly coordinates to pixel coordinates
            const pixelCoords = plotlyToPixelCoords(
                coords.x,
                coords.y,
                deconvolutionDiv,
            );
            if (!pixelCoords) {
                return;
            }

            const centerX = pixelCoords.x;
            const centerY = pixelCoords.y;

            // Check if coordinates are within canvas bounds (with tolerance for pie chart radius)
            if (
                centerX < -radius ||
                centerX > rect.width + radius ||
                centerY < -radius ||
                centerY > rect.height + radius
            ) {
                // Skip spots outside visible area
                return;
            }

            // Calculate total
            const total = pieSlices.reduce(
                (sum, slice) => sum + slice.value,
                0,
            );
            if (total === 0) return;

            // Sort by value descending
            pieSlices.sort((a, b) => b.value - a.value);

            // Draw pie chart
            let currentAngle = -Math.PI / 2; // Start from top

            pieSlices.forEach((slice) => {
                const { value, color } = slice;
                const sliceAngle = (value / total) * 2 * Math.PI;

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

                // Ensure color is set correctly
                const fillColor = color || "#8dd3c7"; // Default to first color in palette
                ctx.fillStyle = fillColor;
                ctx.fill();

                currentAngle += sliceAngle;
            });

            drawnCount++;
        });

        console.log(
            "Drew",
            drawnCount,
            "pie charts out of",
            spots.length,
            "spots",
        );
    }

    // Deconvolution Analysis functions
    async function drawDeconvolutionPlot() {
        if (!deconvolutionData || !deconvolutionDiv || !spatialData) {
            console.warn("Missing data for deconvolution plot");
            return;
        }

        const cellTypes = deconvolutionData.cell_types || [];
        const spots = deconvolutionData.spots || [];

        if (cellTypes.length === 0 || spots.length === 0) {
            return;
        }

        // Create Plotly plot without background image
        // Get coordinate ranges from spatial data
        let minX = Infinity,
            maxX = -Infinity,
            minY = Infinity,
            maxY = -Infinity;
        spatialData.forEach((trace) => {
            const xs = trace.x || [];
            const ys = trace.y || [];
            xs.forEach((x) => {
                if (Number.isFinite(x)) {
                    minX = Math.min(minX, x);
                    maxX = Math.max(maxX, x);
                }
            });
            ys.forEach((y) => {
                if (Number.isFinite(y)) {
                    minY = Math.min(minY, y);
                    maxY = Math.max(maxY, y);
                }
            });
        });

        // Add padding
        const padding = Math.max(
            (maxX - minX) * 0.05,
            (maxY - minY) * 0.05,
            50,
        );
        minX -= padding;
        maxX += padding;
        minY -= padding;
        maxY += padding;

        const layout = {
            autosize: true,
            xaxis: {
                visible: false,
                range: [minX, maxX],
            },
            yaxis: {
                visible: false,
                range: [maxY, minY], // y轴反向
                scaleanchor: "x",
                scaleratio: 1,
            },
            dragmode: "zoom", // Enable zoom
            margin: { l: 0, r: 0, t: 0, b: 0 },
            showlegend: false,
            plot_bgcolor: "transparent",
            paper_bgcolor: "transparent",
        };

        // Create scatter trace with all spots (similar to clusterResultComparison)
        const allX = [];
        const allY = [];
        const allBarcodes = [];

        spatialData.forEach((trace) => {
            const barcodes = trace.customdata || trace.text || [];
            const xs = trace.x || [];
            const ys = trace.y || [];
            barcodes.forEach((barcode, idx) => {
                if (barcode && idx < xs.length && idx < ys.length) {
                    allX.push(xs[idx]);
                    allY.push(ys[idx]);
                    allBarcodes.push(String(barcode).trim());
                }
            });
        });

        // Build hover data for each spot
        const hoverTexts = [];
        const proportions = deconvolutionData.proportions || [];

        // Create a map from barcode to proportions for quick lookup
        const barcodeToProportions = new Map();
        spots.forEach((barcode, idx) => {
            barcodeToProportions.set(
                String(barcode).trim(),
                proportions[idx] || [],
            );
        });

        allBarcodes.forEach((barcode) => {
            const barcodeStr = String(barcode).trim();
            const spotProportions = barcodeToProportions.get(barcodeStr) || [];

            if (spotProportions.length > 0 && cellTypes.length > 0) {
                // Build hover text with cell type proportions
                const parts = [];
                cellTypes.forEach((cellType, idx) => {
                    const prop = spotProportions[idx] || 0;
                    if (prop > 0.01) {
                        // Only show if > 1%
                        parts.push(`${cellType}: ${(prop * 100).toFixed(1)}%`);
                    }
                });
                hoverTexts.push(`<b>${barcode}</b><br>${parts.join("<br>")}`);
            } else {
                hoverTexts.push(`<b>${barcode}</b>`);
            }
        });

        const baseTrace = {
            x: allX,
            y: allY,
            customdata: allBarcodes,
            type: "scatter",
            mode: "markers",
            marker: {
                color: "rgba(255, 255, 255, 0)", // Transparent - pie charts will be drawn on canvas
                size: 6, // Make markers slightly visible for hover detection
            },
            hoverinfo: "text",
            text: hoverTexts,
            hovertemplate: "%{text}<extra></extra>",
        };

        const traces = [baseTrace];

        if (deconvolutionDiv._fullData) {
            Plotly.redraw(deconvolutionDiv);
        } else {
            await Plotly.newPlot(deconvolutionDiv, traces, layout, {
                responsive: true,
                useResizeHandler: true,
                displayModeBar: false,
                scrollZoom: true, // Enable mouse wheel zoom
            });
        }

        // Draw pie charts on canvas overlay after Plotly renders
        setTimeout(() => {
            drawDeconvolutionPieCharts();
        }, 100);
    }

    // Deconvolution Analysis reactive statements
    $: if (
        selectedAnalysis === "deconvolution" &&
        deconvolutionData &&
        deconvolutionDiv &&
        deconvolutionPieCanvas &&
        spatialData
    ) {
        tick().then(() => {
            drawDeconvolutionPlot();
        });
    }
    
    // Reactive: selectedCellTypes changes are handled by DeconvolutionAnalysis component

    // Safely resize a Plotly plot, only when the div is still in the DOM and visible
    function safePlotResize(div) {
        if (!div || !div.isConnected) return;

        // If the element (or its ancestors) are display:none, offsetParent will be null.
        // Also guard against 0-size elements to avoid Plotly throwing.
        const isHidden =
            div.offsetParent === null ||
            div.clientWidth === 0 ||
            div.clientHeight === 0;
        if (isHidden) return;

        try {
            Plotly.Plots.resize(div);
        } catch (err) {
            console.warn(
                "Plotly resize skipped due to non-displayed div:",
                err,
            );
        }
    }

    function observeResize(dom, callback) {
        if (!dom) return;
        const ro = new ResizeObserver(() => {
            // 检查DOM元素是否仍然存在且连接到文档
            if (dom && dom.isConnected && callback) {
                callback();
            }
        });
        ro.observe(dom);
        return () => ro.disconnect();
    }

    // Internal loading state for downstream analysis component
    let isLoadingData = false;

    // Load spatial plot data (only if not already provided via props)
    async function loadSpatialPlotData(forceReload = false) {
        if (!currentSlice || !currentClusterResultId) {
            console.warn(
                "⚠️ Cannot load spatial plot data: missing slice or cluster result ID",
                {
                    currentSlice,
                    currentClusterResultId,
                },
            );
            return;
        }

        // If data is already provided via props and has content, skip loading (unless forceReload is true)
        if (
            !forceReload &&
            spatialData &&
            Array.isArray(spatialData) &&
            spatialData.length > 0 &&
            image
        ) {
            console.log(
                "✅ Spatial plot data already available from props, skipping reload",
                { tracesCount: spatialData.length },
            );
            return;
        }

        // Set loading state
        isLoadingData = true;

        console.log("📥 Loading spatial plot data...", {
            currentSlice,
            currentClusterResultId,
            hasSpatialData: !!spatialData,
            spatialDataLength: Array.isArray(spatialData)
                ? spatialData.length
                : "N/A",
            hasImage: !!image,
        });

        try {
            const imageUrl = `${baseApi}/images/${currentSlice}/tissue_hires_image.png`;

            // Load image only if not provided
            if (!image) {
                console.log("📥 Loading image...");
                image = await new Promise((resolve, reject) => {
                    const img = new Image();
                    img.onload = () => {
                        console.log("✅ Image loaded");
                        resolve(img);
                    };
                    img.onerror = (err) => {
                        console.error("❌ Image load error", err);
                        reject(err);
                    };
                    img.src = imageUrl;
                });
            }

            // Load plot data if not provided, empty, or forceReload is true
            if (
                forceReload ||
                !spatialData ||
                !Array.isArray(spatialData) ||
                spatialData.length === 0
            ) {
                console.log("📥 Loading plot data...", {
                    forceReload,
                    currentClusterResultId,
                });
                const plotRes = await fetch(
                    `${baseApi}/plot-data?slice_id=${currentSlice}&cluster_result_id=${currentClusterResultId}`,
                );
                if (!plotRes.ok) {
                    throw new Error(
                        `Failed to load plot data: ${plotRes.status} ${plotRes.statusText}`,
                    );
                }
                const plotData = await plotRes.json();
                spatialData = plotData || [];
                console.log("✅ Plot data loaded", {
                    tracesCount: spatialData.length,
                    cluster_result_id: currentClusterResultId,
                });

                // After loading plot data, we need to update the color scale to match the actual clusters
                // Extract clusters from plotData and dispatch event to parent to update color scale
                const clustersFromData = Array.from(
                    new Set((plotData || []).map((trace) => `${trace.name}`)),
                ).sort((a, b) => {
                    const numA = parseFloat(a);
                    const numB = parseFloat(b);
                    if (Number.isNaN(numA) || Number.isNaN(numB)) {
                        return `${a}`.localeCompare(`${b}`);
                    }
                    return numA - numB;
                });

                console.log(
                    "🎨 DownstreamAnalysis: Requesting color scale update",
                    {
                        clustersFromData,
                        clusterResultId: currentClusterResultId,
                    },
                );

                // Dispatch event to parent to update color scale
                dispatch("updateColorScale", {
                    clusters: clustersFromData,
                    clusterResultId: currentClusterResultId,
                    sliceId: currentSlice,
                });
                // Don't cancel loading here - wait for plot to render with colors
                // The loading will be canceled when plotRendered event is fired
            }
        } catch (error) {
            console.error("❌ Error loading spatial plot data:", error);
            // Only clear if we don't have props data
            if (
                !spatialData ||
                !Array.isArray(spatialData) ||
                spatialData.length === 0
            ) {
                spatialData = [];
            }
            if (!image) {
                image = null;
            }
            // On error, cancel loading immediately
            isLoadingData = false;
        }
        // Note: isLoadingData will be set to false when plotRendered event is fired
    }

    onMount(() => {
        currentCluster = availableClusters?.length
            ? `${availableClusters[0]}`
            : "1.0";
        console.log("🚀 DownstreamAnalysis onMount", {
            hasSpatialData: !!spatialData,
            spatialDataLength: Array.isArray(spatialData)
                ? spatialData.length
                : "N/A",
            hasImage: !!image,
            currentSlice,
            currentClusterResultId,
        });
        // Only load if data is not provided via props
        // And only if not already loading from parent (to avoid duplicate loading)
        if (
            (!spatialData ||
                !Array.isArray(spatialData) ||
                spatialData.length === 0 ||
                !image) &&
            !isLoadingClusterResult
        ) {
            console.log("📥 onMount: Loading spatial plot data...");
            loadSpatialPlotData();
        } else {
            console.log(
                "✅ onMount: Spatial plot data already available from props or loading from parent",
            );
        }
    });

    let lastSliceId = null;
    let lastClusterResultId = null;

    $: if (availableClusters && availableClusters.length) {
        if (!availableClusters.includes(currentCluster)) {
            currentCluster = `${availableClusters[0]}`;
        }
    }

    $: if (
        (currentSlice && currentSlice !== lastSliceId) ||
        (currentClusterResultId &&
            currentClusterResultId !== lastClusterResultId)
    ) {
        const previousSliceId = lastSliceId;
        const previousClusterResultId = lastClusterResultId;
        lastSliceId = currentSlice;
        lastClusterResultId = currentClusterResultId;
        clearAnalysisCache();
        selectedAnalysis = null; // Also reset the selected analysis when slice/cluster changes

        // When cluster_result_id changes, we need to reload data to get the correct cluster information
        // Even if spatialData exists, it might be from a different cluster_result_id
        // So we should force reload to ensure we have the correct data for the new cluster_result_id
        // But only if we're not already loading (to avoid duplicate loading states)
        if (
            currentClusterResultId &&
            currentClusterResultId !== previousClusterResultId
        ) {
            // Only load if not already loading from parent (isLoadingClusterResult)
            // This prevents duplicate loading when App.svelte is already loading via loadClusterResult
            if (!isLoadingClusterResult) {
                // Force reload with new cluster_result_id to get correct cluster information
                loadSpatialPlotData(true);
            }
        } else if (!spatialData || spatialData.length === 0 || !image) {
            // Only load if props are not available (for initial load or slice change)
            // And only if not already loading from parent
            if (!isLoadingClusterResult) {
                loadSpatialPlotData();
            }
        }
    }

    // Reactive statement to reload plot data when spatialData prop changes from App.svelte
    $: if (
        spatialData &&
        spatialData.length > 0 &&
        image &&
        downstreamPlotInstance
    ) {
        console.log(
            "🔄 DownstreamAnalysis: spatialData updated, triggering redraw",
            {
                spatialDataLength: spatialData.length,
                hasImage: !!image,
                hasInstance: !!downstreamPlotInstance,
            },
        );
        // Force redraw when spatialData is updated from parent
        tick().then(() => {
            if (
                downstreamPlotInstance &&
                typeof downstreamPlotInstance.redraw === "function"
            ) {
                downstreamPlotInstance.redraw();
            }
        });
    }

    // Reactive statement to trigger redraw when clusterColorScale changes
    $: if (clusterColorScaleVersion !== undefined && downstreamPlotInstance) {
        console.log(
            "🎨 DownstreamAnalysis: clusterColorScaleVersion changed, triggering redraw",
            {
                clusterColorScaleVersion,
                hasInstance: !!downstreamPlotInstance,
            },
        );
        tick().then(() => {
            if (
                downstreamPlotInstance &&
                typeof downstreamPlotInstance.redraw === "function"
            ) {
                downstreamPlotInstance.redraw();
            }
        });
    }

    // Debug: log when spatialData prop changes
    $: if (spatialData !== undefined) {
        console.log("📊 DownstreamAnalysis: spatialData prop changed", {
            isArray: Array.isArray(spatialData),
            length: Array.isArray(spatialData) ? spatialData.length : "N/A",
            hasImage: !!image,
            currentClusterResultId,
        });
    }

    // Listen for showAnalysisSelection event from App.svelte
    onMount(() => {
        const handleShowDialog = () => {
            showAnalysisDialog = true;
        };
        document.addEventListener("showAnalysisSelection", handleShowDialog);
        return () => {
            document.removeEventListener(
                "showAnalysisSelection",
                handleShowDialog,
            );
        };
    });

    // Reactive statement to render charts when analysis data is loaded and divs are ready
    $: if (loadedAnalyses.length > 0) {
        loadedAnalyses.forEach((analysis) => {
            if (analysis.data && !analysis.chartRendered) {
                // Check if all required divs are ready
                const divsReady =
                    (analysis.type === "svg" &&
                        analysis.chartDiv &&
                        analysis.chartDiv2) ||
                    // Spatial: cluster bubble uses chartDiv3; ligand-receptor bubble uses chartDiv5
                    (analysis.type === "spatial" &&
                        analysis.chartDiv3 &&
                        analysis.chartDiv5) ||
                    (analysis.type === "deg" &&
                        analysis.chartDiv &&
                        analysis.chartDiv2) ||
                    (analysis.type === "deconvolution" && analysis.data);

                if (divsReady) {
                    console.log(
                        "🎨 Rendering charts for analysis:",
                        analysis.type,
                        analysis.id,
                    );
                    // Use a small delay to ensure DOM is fully ready
                    setTimeout(() => {
                        tick().then(() => {
                            renderAllAnalysisCharts(analysis)
                                .then(() => {
                                    console.log(
                                        "✅ Charts rendered for:",
                                        analysis.type,
                                        analysis.id,
                                    );
                                    const index = loadedAnalyses.findIndex(
                                        (a) => a.id === analysis.id,
                                    );
                                    if (
                                        index !== -1 &&
                                        !loadedAnalyses[index].chartRendered
                                    ) {
                                        loadedAnalyses[index] = {
                                            ...loadedAnalyses[index],
                                            chartRendered: true,
                                        };
                                        loadedAnalyses = loadedAnalyses;
                                    }
                                })
                                .catch((err) => {
                                    console.error(
                                        "❌ Error rendering charts:",
                                        err,
                                    );
                                });
                        });
                    }, 100);
                } else {
                    // Log which divs are missing
                    if (analysis.type === "svg") {
                        console.log("⏳ Waiting for divs - SVG:", {
                            chartDiv: !!analysis.chartDiv,
                            chartDiv2: !!analysis.chartDiv2,
                        });
                    } else if (analysis.type === "spatial") {
                        console.log("⏳ Waiting for divs - Spatial:", {
                            chartDiv3: !!analysis.chartDiv3,
                            chartDiv4: !!analysis.chartDiv4,
                            chartDiv5: !!analysis.chartDiv5,
                            chartDiv6: !!analysis.chartDiv6,
                        });
                    } else if (analysis.type === "deg") {
                        console.log("⏳ Waiting for divs - DEG:", {
                            chartDiv: !!analysis.chartDiv,
                            chartDiv2: !!analysis.chartDiv2,
                        });
                    }
                }
            }
        });
    }

    // Modified selectAnalysis to add to loadedAnalyses instead of replacing
    function addAnalysis(analysisId, selectedCluster = null) {
        const analysis = analysisTypes.find((a) => a.id === analysisId);
        if (!analysis) return;

        // For SVG analysis, require cluster selection
        if (analysisId === "svg" && !selectedCluster) {
            pendingSVGAnalysis = true; // Mark that we're waiting for cluster selection
            selectedSVGClusterInDialog = null;
            return;
        }

        // Check if already loaded (only check if we have a selectedCluster for SVG)
        // For SVG, we allow multiple analyses with different clusters
        if (analysisId === "svg" && selectedCluster) {
            // Check if this specific cluster's SVG analysis is already loaded
            if (
                loadedAnalyses.find(
                    (a) =>
                        a.type === analysisId &&
                        a.selectedCluster === selectedCluster,
                )
            ) {
                showAnalysisDialog = false;
                pendingSVGAnalysis = false;
                selectedSVGClusterInDialog = null;
                return;
            }
        } else if (analysisId !== "svg") {
            // For non-SVG analyses, check if already loaded (no cluster distinction)
            if (
                loadedAnalyses.find(
                    (a) => a.type === analysisId,
                )
            ) {
                showAnalysisDialog = false;
                pendingSVGAnalysis = false;
                selectedSVGClusterInDialog = null;
                return;
            }
        }

        // Add to loaded analyses
        const newAnalysis = {
            id: `analysis-${Date.now()}`,
            type: analysisId,
            name: analysis.name,
            description: analysis.description,
            loading: true,
            data: null,
            error: null,
            selectedCluster: selectedCluster || null,
            // Chart divs for different modes
            chartDiv: null, // Main chart div (for SVG Bar, DEG Heatmap, Deconvolution Plotly, etc.)
            chartDiv2: null, // Second chart div (for SVG Sankey, DEG Dotplot, etc.)
            pieCanvas: null, // Canvas overlay for deconvolution pie charts
            icicleDiv: null, // Div for deconvolution icicle chart
            chartDiv3: null, // Third chart div (for Spatial Communication Cluster Number)
            chartDiv4: null, // Fourth chart div (for Spatial Communication Cluster Strength)
            chartDiv5: null, // Fifth chart div (for Spatial Communication Ligand-Receptor Number)
            chartDiv6: null, // Sixth chart div (for Spatial Communication Ligand-Receptor Strength)
            chartRendered: false,
            currentMode:
                analysisId === "svg"
                    ? "Bar"
                    : analysisId === "deg"
                      ? "Heatmap"
                      : analysisId === "spatial"
                        ? "Cluster"
                        : null,
            clusterSubMode: "Number", // For Spatial Communication Cluster mode: Number or Strength
            ligandReceptorSubMode: "Number", // For Spatial Communication Ligand-Receptor mode: Number or Strength
        };
        loadedAnalyses = [...loadedAnalyses, newAnalysis];
        showAnalysisDialog = false;
        pendingSVGAnalysis = false;
        selectedSVGClusterInDialog = null;

        // Load the analysis data
        if (analysisId === "spatial") {
            loadSpatialCommunication()
                .then(() => {
                    const index = loadedAnalyses.findIndex(
                        (a) => a.id === newAnalysis.id,
                    );
                    if (index !== -1) {
                        loadedAnalyses[index] = {
                            ...loadedAnalyses[index],
                            loading: false,
                            data: cellChat,
                        };
                        loadedAnalyses = loadedAnalyses; // Trigger reactivity
                    }
                })
                .catch((err) => {
                    const index = loadedAnalyses.findIndex(
                        (a) => a.id === newAnalysis.id,
                    );
                    if (index !== -1) {
                        loadedAnalyses[index] = {
                            ...loadedAnalyses[index],
                            loading: false,
                            error: err.message || "Failed to load analysis",
                        };
                        loadedAnalyses = loadedAnalyses;
                    }
                });
        } else if (analysisId === "deg") {
            loadDEGAnalysis()
                .then(() => {
                    const index = loadedAnalyses.findIndex(
                        (a) => a.id === newAnalysis.id,
                    );
                    if (index !== -1) {
                        loadedAnalyses[index] = {
                            ...loadedAnalyses[index],
                            loading: false,
                            data: { clusterGeneExpression, clusterGeneDot },
                        };
                        loadedAnalyses = loadedAnalyses;
                    }
                })
                .catch((err) => {
                    const index = loadedAnalyses.findIndex(
                        (a) => a.id === newAnalysis.id,
                    );
                    if (index !== -1) {
                        loadedAnalyses[index] = {
                            ...loadedAnalyses[index],
                            loading: false,
                            error: err.message || "Failed to load analysis",
                        };
                        loadedAnalyses = loadedAnalyses;
                    }
                });
        } else if (analysisId === "deconvolution") {
            loadDeconvolutionAnalysis()
                .then(() => {
                    const index = loadedAnalyses.findIndex(
                        (a) => a.id === newAnalysis.id,
                    );
                    if (index !== -1) {
                        loadedAnalyses[index] = {
                            ...loadedAnalyses[index],
                            loading: false,
                            data: deconvolutionData,
                        };
                        loadedAnalyses = loadedAnalyses;
                    }
                })
                .catch((err) => {
                    const index = loadedAnalyses.findIndex(
                        (a) => a.id === newAnalysis.id,
                    );
                    if (index !== -1) {
                        loadedAnalyses[index] = {
                            ...loadedAnalyses[index],
                            loading: false,
                            error: err.message || "Failed to load analysis",
                        };
                        loadedAnalyses = loadedAnalyses;
                    }
                });
        } else if (analysisId === "svg" && selectedCluster) {
            // Load SVG analysis for selected cluster
            loadSVGAnalysis(selectedCluster, true)
                .then(() => {
                    const index = loadedAnalyses.findIndex(
                        (a) => a.id === newAnalysis.id,
                    );
                    if (index !== -1) {
                        loadedAnalyses[index] = {
                            ...loadedAnalyses[index],
                            loading: false,
                            data: hvg[selectedCluster]
                                ? {
                                      cluster: selectedCluster,
                                      hvg: hvg[selectedCluster],
                                  }
                                : null,
                        };
                        loadedAnalyses = loadedAnalyses;
                    }
                })
                .catch((err) => {
                    const index = loadedAnalyses.findIndex(
                        (a) => a.id === newAnalysis.id,
                    );
                    if (index !== -1) {
                        loadedAnalyses[index] = {
                            ...loadedAnalyses[index],
                            loading: false,
                            error: err.message || "Failed to load SVG analysis",
                        };
                        loadedAnalyses = loadedAnalyses;
                    }
                });
        } else if (analysisId === "neighborhood") {
            // Neighborhood needs region selection
            newAnalysis.needsRegionSelection = selectedRegionCount === 0;
            newAnalysis.loading = false;
            loadedAnalyses = loadedAnalyses;
        }
    }

    function removeAnalysis(analysisId) {
        loadedAnalyses = loadedAnalyses.filter((a) => a.id !== analysisId);
    }

    async function analyzeWithKimi(analysisId) {
        const analysis = loadedAnalyses.find((a) => a.id === analysisId);
        if (!analysis || !analysis.data) {
            console.warn("No analysis data available for AI assistant analysis");
            return;
        }

        // Set loading state
        const index = loadedAnalyses.findIndex((a) => a.id === analysisId);
        if (index !== -1) {
            loadedAnalyses[index] = {
                ...loadedAnalyses[index],
                kimiLoading: true,
                kimiError: null,
            };
            loadedAnalyses = loadedAnalyses;
        }

        try {
            // Prepare complete analysis data - include all views/modes for comprehensive analysis
            let analysisDataToSend = {};
            let additionalContext = [];
            
            if (analysis.type === "svg") {
                // For SVG, send the complete hvg data (used for both Bar and Sankey views)
                analysisDataToSend = analysis.data.hvg || analysis.data;
                if (analysis.selectedCluster) {
                    additionalContext.push(`Cluster ${analysis.selectedCluster} SVG analysis`);
                }
                // SVG has two views: Bar (Enrichment) and Sankey (Gene-Pathway)
                // Both use the same hvg data, so we send all of it
                additionalContext.push("This analysis includes both Enrichment (Bar chart) and Gene-Pathway (Sankey diagram) views");
            } else if (analysis.type === "spatial") {
                // For spatial communication, send complete cellChat data
                // This includes both Cluster and Ligand-Receptor mode data
                analysisDataToSend = analysis.data;
                const currentMode = analysis.currentMode || "Cluster";
                additionalContext.push(`Current view mode: ${currentMode}`);
                additionalContext.push("This analysis includes both Cluster-level and Ligand-Receptor interaction data");
            } else if (analysis.type === "deg") {
                // For DEG, send both expression (Heatmap) and dot (Dotplot) data
                // Normalize to backend-expected keys: "expression" and "dot"
                analysisDataToSend = {
                    expression: analysis.data.clusterGeneExpression,
                    dot: analysis.data.clusterGeneDot,
                };
                const currentMode = analysis.currentMode || "Heatmap";
                additionalContext.push(`Current view mode: ${currentMode}`);
                additionalContext.push("This analysis includes both Heatmap and Dotplot views with complete gene expression data");
            } else if (analysis.type === "deconvolution") {
                // For deconvolution, send the complete deconvolution data
                // This includes cell type proportions, hierarchy, and all visualization data
                analysisDataToSend = analysis.data;
                additionalContext.push("This analysis includes cell type proportions, hierarchy, and spatial distribution data");
            } else {
                // For other types, send the data as is
                analysisDataToSend = analysis.data;
            }

            const response = await fetch(`${baseApi}/gemini-analyze`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    analysis_type: analysis.type,
                    analysis_data: analysisDataToSend,
                    slice_id: currentSlice,
                    cluster_result_id: currentClusterResultId,
                    additional_context: additionalContext.length > 0 ? additionalContext.join(". ") : null,
                }),
            });

            if (!response.ok) {
                const errorText = await response.text();
                throw new Error(
                    `HTTP ${response.status}: ${errorText || response.statusText}`,
                );
            }

            const result = await response.json();

            // Update analysis with AI assistant result
            if (index !== -1) {
                loadedAnalyses[index] = {
                    ...loadedAnalyses[index],
                    kimiAnalysis: result.analysis,
                    kimiLoading: false,
                    kimiError: result.status === "error" ? result.error : null,
                };
                loadedAnalyses = loadedAnalyses;
            }
        } catch (error) {
            console.error("Error calling AI assistant analysis:", error);
            if (index !== -1) {
                loadedAnalyses[index] = {
                    ...loadedAnalyses[index],
                    kimiLoading: false,
                    kimiError:
                        error.message ||
                        "Failed to analyze with the AI assistant",
                };
                loadedAnalyses = loadedAnalyses;
            }
        }
    }

    // Function to render all charts for an analysis
    async function renderAllAnalysisCharts(analysis) {
        if (!analysis.data) {
            console.warn(
                "⚠️ No data for analysis:",
                analysis.type,
                analysis.id,
            );
            return;
        }

        console.log(
            "🔄 renderAllAnalysisCharts called for:",
            analysis.type,
            analysis.id,
            {
                hasData: !!analysis.data,
                dataKeys: analysis.data ? Object.keys(analysis.data) : [],
                chartDivs: {
                    chartDiv: !!analysis.chartDiv,
                    chartDiv2: !!analysis.chartDiv2,
                    chartDiv3: !!analysis.chartDiv3,
                    chartDiv4: !!analysis.chartDiv4,
                },
            },
        );

        await tick(); // Wait for DOM to be ready

        if (analysis.type === "svg") {
            // SVG Analysis - render both Bar and Sankey charts
            console.log("📊 SVG Analysis data:", {
                hasHvg: !!analysis.data.hvg,
                isArray: Array.isArray(analysis.data.hvg),
                length: Array.isArray(analysis.data.hvg)
                    ? analysis.data.hvg.length
                    : 0,
            });
            if (analysis.data.hvg && Array.isArray(analysis.data.hvg)) {
                if (analysis.chartDiv) {
                    console.log("📈 Drawing Bar chart to div");
                    drawEnrichmentChartToDiv(
                        analysis.data.hvg,
                        analysis.chartDiv,
                    );
                } else {
                    console.warn("⚠️ chartDiv not available for SVG Bar chart");
                }
                if (analysis.chartDiv2) {
                    console.log("📈 Drawing Sankey chart to div");
                    // Render Sankey chart
                    const topTermCount = 10;
                    const topGenePerTerm = 5;
                    const topTerms = [...analysis.data.hvg]
                        .sort(
                            (a, b) =>
                                a["Adjusted P-value"] - b["Adjusted P-value"],
                        )
                        .slice(0, topTermCount);
                    const filteredResults = topTerms.map((term) => {
                        const geneList = term.Genes.split(";").map((g) =>
                            g.trim(),
                        );
                        const topGenes = geneList.slice(0, topGenePerTerm);
                        return {
                            ...term,
                            Genes: topGenes.join(";"),
                        };
                    });
                    drawSankeyToDiv(filteredResults, analysis.chartDiv2);
                } else {
                    console.warn(
                        "⚠️ chartDiv2 not available for SVG Sankey chart",
                    );
                }
            } else {
                console.warn(
                    "⚠️ SVG data.hvg is not an array:",
                    analysis.data.hvg,
                );
            }
        } else if (analysis.type === "spatial") {
            // Spatial Communication
            // Cluster mode: use a single bubble plot (from -> to, size = number, color = strength)
            if (
                analysis.data &&
                analysis.data.cluster_names &&
                analysis.chartDiv3
            ) {
                await renderSpatialCommunicationBubbleToDiv(
                    analysis.data,
                    analysis.chartDiv3,
                );
            }
            // Ligand-Receptor mode: use a single bubble plot as well
            if (
                analysis.data &&
                analysis.data.top_interactions &&
                analysis.chartDiv5
            ) {
                await renderLigandReceptorBubbleToDiv(
                    analysis.data,
                    analysis.chartDiv5,
                );
            }
        } else if (analysis.type === "deg") {
            // DEG Analysis - render both heatmap and dotplot
            if (analysis.data.clusterGeneExpression && analysis.chartDiv) {
                renderDEGHeatmapToDiv(
                    analysis.data.clusterGeneExpression,
                    analysis.chartDiv,
                );
            }
            if (analysis.data.clusterGeneDot && analysis.chartDiv2) {
                renderDEGDotplotToDiv(
                    analysis.data.clusterGeneDot,
                    analysis.chartDiv2,
                );
            }
        } else if (analysis.type === "deconvolution") {
            // Deconvolution Analysis is handled by DeconvolutionAnalysis component
            // No manual rendering needed here
        }
    }

    // Helper functions to render charts to specific divs
    async function drawEnrichmentChartToDiv(results, divElement) {
        if (!Array.isArray(results) || !divElement) return;

        // Clear existing plot
        Plotly.purge(divElement);

        // Wait for DOM to be ready before getting container dimensions
        await tick();

        const clusters = [...new Set(results.map((d) => d.Category))];
        const colorScale = d3.scaleOrdinal(d3.schemeTableau10).domain(clusters);
        const maxLabelLength = 60;
        const yLabels = [...results].map((d) =>
            d.Term.length > maxLabelLength
                ? d.Term.slice(0, maxLabelLength - 3) + "..."
                : d.Term,
        );
        const tracesMap = new Map();
        for (const d of results) {
            const label =
                d.Term.length > maxLabelLength
                    ? d.Term.slice(0, maxLabelLength - 3) + "..."
                    : d.Term;
            if (!tracesMap.has(d.Category)) {
                tracesMap.set(d.Category, {
                    type: "bar",
                    name: d.Category,
                    x: [],
                    y: [],
                    orientation: "h",
                    text: [],
                    textposition: "none",
                    customdata: [],
                    hovertemplate:
                        "<b>%{text}</b><br>Category: " +
                        d.Category +
                        "<br>-log10(p-adj): %{x:.2f}<br>Genes: %{customdata}<extra></extra>",
                    marker: { color: colorScale(d.Category) },
                });
            }
            const trace = tracesMap.get(d.Category);
            trace.x.push(-Math.log10(d["Adjusted P-value"]));
            trace.y.push(label);
            trace.text.push(d.Term);
            trace.customdata.push(d.Genes.split(";").length);
        }
        const traces = [...tracesMap.values()];

        // Get container dimensions for responsive sizing
        const containerWidth = divElement.clientWidth || undefined;
        const containerHeight = divElement.clientHeight || 256;

        const layout = {
            showlegend: true,
            title: "Top Enriched Terms",
            barmode: "group",
            margin: { l: 150, r: 10, t: 80, b: 20 },
            autosize: true,
            height: containerHeight,
            width: containerWidth,
            yaxis: {
                categoryorder: "array",
                categoryarray: yLabels,
                automargin: true,
                tickfont: { size: 10 },
            },
            xaxis: {
                title: "-log10(Adjusted P-value)",
                tickfont: { size: 10 },
            },
            legend: {
                // Place legend above the bars, laid out horizontally.
                // Use a small negative x so the first legend item aligns with the left edge of y-axis text.
                orientation: "h",
                x: -0.1,
                y: 1.1,
                xanchor: "left",
                yanchor: "bottom",
                bgcolor: "rgba(255, 255, 255, 0.8)",
                bordercolor: "rgba(0, 0, 0, 0.2)",
                borderwidth: 1,
                traceorder: "normal",
                itemwidth: 30,
                font: { size: 10 },
            },
        };
        Plotly.newPlot(divElement, traces, layout, {
            scrollZoom: false,
            responsive: true,
            useResizeHandler: true,
            displaylogo: false,
            modeBarButtons: [["pan2d", "resetScale2d", "toImage"]],
        });

        // Add resize observer for container size changes
        observeResize(divElement, () => {
            safePlotResize(divElement);
        });

        // Also listen to window resize events
        window.addEventListener("resize", () => {
            safePlotResize(divElement);
        });
    }

    async function drawSankeyToDiv(results, divElement) {
        if (!results || !divElement || !Array.isArray(results)) return;
        Plotly.purge(divElement);

        // Wait for DOM to be ready
        await tick();

        const geneSet = new Set();
        const termSet = [];
        const links = [];
        results.forEach((item) => {
            const genes = item.Genes.split(";");
            const term = item.Term;
            termSet.push(term);
            genes.forEach((g) => {
                geneSet.add(g);
                links.push({ source: g, target: term });
            });
        });
        const genes = [...geneSet];
        const terms = [...new Set(termSet)];
        const nodes = genes.concat(terms);
        const nodeIndex = Object.fromEntries(nodes.map((n, i) => [n, i]));
        const pastelColors = [
            "#aec6cf",
            "#ffb347",
            "#77dd77",
            "#f49ac2",
            "#cfcfc4",
            "#b39eb5",
            "#ff6961",
            "#cb99c9",
            "#fdfd96",
            "#836953",
        ];
        const lightColors = [
            "#b3cde0",
            "#decbe4",
            "#fed9a6",
            "#ccebc5",
            "#fbb4ae",
            "#e5d8bd",
            "#f2f2f2",
            "#d9d9d9",
            "#e6f5c9",
            "#fddaec",
        ];
        const geneColorScale = d3.scaleOrdinal(pastelColors);
        const termColorScale = d3.scaleOrdinal(lightColors);
        const nodeColors = nodes.map((n, i) =>
            i < genes.length ? geneColorScale(n) : termColorScale(n),
        );
        const nodeColorMap = Object.fromEntries(
            nodes.map((n, i) => [n, nodeColors[i]]),
        );
        function hexToRgb(hex) {
            hex = hex.replace("#", "");
            const bigint = parseInt(hex, 16);
            const r = (bigint >> 16) & 255;
            const g = (bigint >> 8) & 255;
            const b = bigint & 255;
            return [r, g, b];
        }
        const data = {
            type: "sankey",
            orientation: "h",
            node: {
                pad: 6,
                thickness: 20,
                line: { color: "black", width: 0.5 },
                label: nodes,
                color: nodeColors,
            },
            link: {
                source: links.map((l) => nodeIndex[l.source]),
                target: links.map((l) => nodeIndex[l.target]),
                value: links.map(() => 1),
                color: links.map((l) => {
                    const hex = geneColorScale(l.source);
                    const [r, g, b] = hexToRgb(hex);
                    return `rgba(${r}, ${g}, ${b}, 0.5)`;
                }),
            },
        };
        // Get container dimensions for responsive sizing
        const containerWidth = divElement.clientWidth || undefined;
        const containerHeight = divElement.clientHeight || 256;

        const layout = {
            title: "Sankey: Genes → Enriched Terms",
            font: { size: 10 },
            margin: { l: 20, r: 20, t: 40, b: 10 },
            autosize: true,
            height: containerHeight,
            width: containerWidth,
        };
        Plotly.newPlot(divElement, [data], layout, {
            displaylogo: false,
            responsive: true,
            useResizeHandler: true,
        });

        // Add resize observer for container size changes
        observeResize(divElement, () => {
            safePlotResize(divElement);
        });

        // Also listen to window resize events
        window.addEventListener("resize", () => {
            safePlotResize(divElement);
        });
    }

    // Spatial communication (cluster-level) bubble plot:
    // x = from cluster, y = to cluster, bubble size = number of interactions, color = strength
    async function renderSpatialCommunicationBubbleToDiv(
        cellChatData,
        divElement,
    ) {
        if (
            !cellChatData ||
            !divElement ||
            !cellChatData.number_matrix ||
            !cellChatData.strength_matrix ||
            !cellChatData.cluster_names
        ) {
            console.warn(
                "renderSpatialCommunicationBubbleToDiv: missing data or div",
                {
                    hasData: !!cellChatData,
                    hasDiv: !!divElement,
                    hasNumberMatrix: !!cellChatData?.number_matrix,
                    hasStrengthMatrix: !!cellChatData?.strength_matrix,
                    hasClusterNames: !!cellChatData?.cluster_names,
                },
            );
            return;
        }

        Plotly.purge(divElement);
        d3.select(divElement).selectAll("*").remove();

        const names = cellChatData.cluster_names || [];
        const numberMatrix = (cellChatData.number_matrix || []).map((row) => [
            ...row,
        ]);
        const strengthMatrix = (cellChatData.strength_matrix || []).map(
            (row) => [...row],
        );

        const n = numberMatrix.length;
        if (!n || strengthMatrix.length !== n) return;

        // Determine active clusters (same logic as filterEmptyClusters, but we need indices too)
        const hasCommunication = Array(n).fill(false);
        for (let i = 0; i < n; i++) {
            const outgoing = numberMatrix[i].some((val) => val > 0);
            const incoming = numberMatrix.some((row) => row[i] > 0);
            hasCommunication[i] = outgoing || incoming;
        }

        const activeIndices = [];
        for (let i = 0; i < n; i++) {
            if (hasCommunication[i]) activeIndices.push(i);
        }
        if (activeIndices.length === 0) return;

        const activeNames = activeIndices.map((idx) => names[idx]);

        // Flatten matrices into bubble data
        const x = [];
        const y = [];
        const sizes = [];
        const colors = [];

        activeIndices.forEach((iNew, ii) => {
            activeIndices.forEach((jNew, jj) => {
                const num = numberMatrix[iNew][jNew];
                const str = strengthMatrix[iNew][jNew];
                if (!num && !str) return;
                x.push(activeNames[ii]); // from
                y.push(activeNames[jj]); // to
                sizes.push(num || 0);
                colors.push(str || 0);
            });
        });

        if (!x.length) {
            console.warn(
                "Spatial bubble plot: no non-zero entries to display.",
            );
            return;
        }

        // Calculate bubble sizes - ensure minimum size for visibility
        const maxSize = Math.max(...sizes.filter((v) => v > 0)) || 1;
        const minSize = Math.min(...sizes.filter((v) => v > 0)) || 1;

        console.log("Rendering spatial communication bubble plot:", {
            dataPoints: x.length,
            activeClusters: activeNames.length,
            maxSize,
            containerSize: {
                width: divElement.clientWidth,
                height: divElement.clientHeight,
            },
        });

        // Scale sizes to be visible (target max ~40px radius)
        const sizeScale = d3.scaleSqrt().domain([0, maxSize]).range([8, 40]);

        const scaledSizes = sizes.map((s) => (s > 0 ? sizeScale(s) : 0));

        // Wait for DOM to be ready so container has correct size
        await tick();

        let containerWidth = divElement.clientWidth;
        if (!containerWidth && divElement.parentElement) {
            containerWidth = divElement.parentElement.clientWidth;
        }
        if (!containerWidth) containerWidth = 400;

        let containerHeight = divElement.clientHeight;
        if (!containerHeight && divElement.parentElement) {
            containerHeight = divElement.parentElement.clientHeight;
        }
        if (!containerHeight) containerHeight = 360;

        const trace = {
            type: "scatter",
            mode: "markers",
            x,
            y,
            marker: {
                size: scaledSizes,
                sizemode: "diameter",
                sizemin: 4,
                color: colors,
                colorscale: "Viridis",
                showscale: true,
                colorbar: {
                    title: "Strength",
                    x: 1.02,
                    y: 0.5,
                    xanchor: "left",
                    yanchor: "middle",
                    len: 0.7,
                    thickness: 18,
                },
                line: {
                    width: 1,
                    color: "rgba(255, 255, 255, 0.8)",
                },
                opacity: 0.8,
            },
            hovertemplate:
                "<b>From</b>: %{x}<br>" +
                "<b>To</b>: %{y}<br>" +
                "<b>Number</b>: %{customdata:.0f}<br>" +
                "<b>Strength</b>: %{marker.color:.2f}<extra></extra>",
            customdata: sizes,
            showlegend: false,
        };

        const layout = {
            title: "Cluster Communication (size = Number, color = Strength)",
            // Let Plotly fully autosize to the container; resize handler will keep it in sync
            autosize: true,
            width: undefined,
            height: undefined,
            // Compact margins so the bubbles occupy almost all available space
            margin: { l: 40, r: 30, t: 30, b: 50 },
            xaxis: {
                title: "From",
                type: "category",
                categoryorder: "array",
                categoryarray: activeNames,
                automargin: true,
                tickangle: -45,
            },
            yaxis: {
                title: "To",
                type: "category",
                categoryorder: "array",
                categoryarray: activeNames,
                automargin: true,
            },
        };

        Plotly.newPlot(divElement, [trace], layout, {
            displaylogo: false,
            responsive: true,
            scrollZoom: true,
        });

        // Keep in sync with container size
        observeResize(divElement, () => {
            safePlotResize(divElement);
        });
        window.addEventListener("resize", () => {
            safePlotResize(divElement);
        });
    }

    function renderSpatialCommunicationStrengthToDiv(cellChatData, divElement) {
        if (!cellChatData || !divElement || !cellChatData.strength_matrix)
            return;
        d3.select(divElement).selectAll("*").remove();
        const width = 350;
        const height = 350;
        const innerRadius = Math.min(width, height) * 0.4;
        const outerRadius = innerRadius + 6;
        let names = cellChatData.cluster_names || [];
        let matrix = (cellChatData.strength_matrix || []).map((row) => [
            ...row,
        ]);
        matrix = getTopNMatrix(matrix, 10);
        const filtered = filterEmptyClusters(matrix, names);
        matrix = filtered.matrix;
        names = filtered.names;
        if (matrix.length === 0 || names.length === 0) return;
        const groupScores = matrix.map(
            (row, i) =>
                row.reduce((a, b) => a + b, 0) +
                matrix.reduce((a, b) => a + b[i], 0),
        );
        const chord = d3
            .chordDirected()
            .padAngle(12 / innerRadius)
            .sortSubgroups(d3.descending)
            .sortChords(d3.descending);
        const chords = chord(matrix);
        chords.groups.forEach((d) => {
            d.name = names[d.index];
        });
        chords.forEach((d) => {
            d.source.name = names[d.source.index];
            d.target.name = names[d.target.index];
        });
        const radiusScale = d3
            .scaleSqrt()
            .domain([0, d3.max(groupScores)])
            .range([innerRadius, innerRadius + 15]);
        const arc = d3
            .arc()
            .innerRadius(innerRadius)
            .outerRadius((d) => radiusScale(groupScores[d.index]));
        const ribbon = d3
            .ribbonArrow()
            .radius(innerRadius - 0.5)
            .padAngle(1 / innerRadius);
        const padding = 20;
        const svg = d3
            .select(divElement)
            .append("svg")
            .attr("width", width)
            .attr("height", height)
            .attr("viewBox", [
                -(width / 2 + padding),
                -(height / 2 + padding),
                width + 2 * padding,
                height + 2 * padding,
            ])
            .attr(
                "style",
                "width: 100%; height: 100%; font: 10px sans-serif; overflow: visible;",
            );
        const textId = "text-path-id-str-" + (divElement.id || Date.now());
        svg.append("path")
            .attr("id", textId)
            .attr("fill", "none")
            .attr(
                "d",
                d3.arc()({ outerRadius, startAngle: 0, endAngle: 2 * Math.PI }),
            );
        svg.append("g")
            .attr("fill-opacity", 0.75)
            .selectAll("path")
            .data(chords)
            .join("path")
            .attr("d", ribbon)
            .attr("fill", (d) =>
                clusterColorScale ? clusterColorScale(d.source.name) : "#ccc",
            )
            .style("mix-blend-mode", "multiply")
            .append("title")
            .text((d) => {
                const sourceName = names[d.source.index];
                const targetName = names[d.target.index];
                const value = matrix[d.source.index][d.target.index];
                return `Cluster: ${sourceName} > ${targetName}\nStrength: ${value.toFixed(2)}`;
            });
        const g = svg.append("g").selectAll("g").data(chords.groups).join("g");
        g.append("path")
            .attr("d", arc)
            .attr("fill", (d) =>
                clusterColorScale ? clusterColorScale(d.name) : "#ccc",
            )
            .attr("stroke", "#fff");
        g.append("text")
            .attr("dy", -3)
            .append("textPath")
            .attr("xlink:href", `#${textId}`)
            .attr("startOffset", (d) => d.startAngle * outerRadius)
            .text((d) => names[d.index]);
    }

    // Ligand-Receptor bubble plot:
    // x = ligand, y = receptor, bubble size = count, color = interaction strength
    async function renderLigandReceptorBubbleToDiv(cellChatData, divElement) {
        if (!cellChatData || !divElement || !cellChatData.top_interactions) {
            console.warn(
                "renderLigandReceptorBubbleToDiv: missing data or div",
                {
                    hasData: !!cellChatData,
                    hasDiv: !!divElement,
                    hasTopInteractions: !!cellChatData?.top_interactions,
                },
            );
            return;
        }

        Plotly.purge(divElement);
        d3.select(divElement).selectAll("*").remove();

        const topInteractions = cellChatData.top_interactions.slice(0, 50);
        if (topInteractions.length === 0) return;

        const ligands = [...new Set(topInteractions.map((d) => d.ligand))];
        const receptors = [...new Set(topInteractions.map((d) => d.receptor))];
        if (!ligands.length || !receptors.length) return;

        // Aggregate counts and strengths by ligand–receptor pair
        const pairMap = new Map();
        topInteractions.forEach((d) => {
            const key = `${d.ligand}||${d.receptor}`;
            const prev = pairMap.get(key) || {
                ligand: d.ligand,
                receptor: d.receptor,
                count: 0,
                strengthSum: 0,
                n: 0,
            };
            prev.count += 1;
            const s =
                typeof d.interaction_score === "number"
                    ? d.interaction_score
                    : 0;
            prev.strengthSum += s;
            prev.n += 1;
            pairMap.set(key, prev);
        });

        const pairs = [...pairMap.values()];
        const x = pairs.map((p) => p.ligand);
        const y = pairs.map((p) => p.receptor);
        const counts = pairs.map((p) => p.count);
        const strengths = pairs.map((p) => (p.n ? p.strengthSum / p.n : 0));

        // Compute visible bubble sizes
        const positiveCounts = counts.filter((v) => v > 0);
        const maxCount = Math.max(...positiveCounts) || 1;
        const minCount = Math.min(...positiveCounts) || maxCount;

        // Use [minCount, maxCount] as domain so relative differences更明显
        // 只要有至少两个不同的 count，就用 [min, max]；否则退回 [0, max]
        const domainMin = minCount < maxCount ? minCount : 0;
        const domainMax = maxCount;

        const sizeScale = d3
            .scaleSqrt()
            .domain([domainMin, domainMax])
            .range([4, 20]); // 缩小最大半径，避免气泡过大

        const scaledSizes = counts.map((c) => (c > 0 ? sizeScale(c) : 0));

        // Wait for DOM to be ready so container has correct size
        await tick();

        let containerWidth = divElement.clientWidth;
        if (!containerWidth && divElement.parentElement) {
            containerWidth = divElement.parentElement.clientWidth;
        }
        if (!containerWidth) containerWidth = 400;

        let containerHeight = divElement.clientHeight;
        if (!containerHeight && divElement.parentElement) {
            containerHeight = divElement.parentElement.clientHeight;
        }
        if (!containerHeight) containerHeight = 360;

        const trace = {
            type: "scatter",
            mode: "markers",
            x,
            y,
            marker: {
                size: scaledSizes,
                sizemode: "diameter",
                sizemin: 4,
                color: strengths,
                colorscale: "Viridis",
                showscale: true,
                colorbar: {
                    title: "Interaction strength",
                    x: 1.02,
                    y: 0.5,
                    xanchor: "left",
                    yanchor: "middle",
                    len: 0.7,
                    thickness: 18,
                },
                line: {
                    width: 1,
                    color: "rgba(255, 255, 255, 0.8)",
                },
                opacity: 0.8,
            },
            hovertemplate:
                "<b>Ligand</b>: %{x}<br>" +
                "<b>Receptor</b>: %{y}<br>" +
                "<b>Count</b>: %{customdata[0]:.0f}<br>" +
                "<b>Strength</b>: %{customdata[1]:.4f}<extra></extra>",
            customdata: pairs.map((p) => [
                p.count,
                p.n ? p.strengthSum / p.n : 0,
            ]),
            showlegend: false,
        };

        const layout = {
            title: "Ligand–Receptor Communication (size = Count, color = Strength)",
            autosize: true,
            width: undefined,
            height: undefined,
            margin: { l: 80, r: 40, t: 40, b: 80 },
            xaxis: {
                title: "Ligand",
                type: "category",
                categoryorder: "array",
                categoryarray: ligands,
                automargin: true,
                tickangle: -45,
            },
            yaxis: {
                title: "Receptor",
                type: "category",
                categoryorder: "array",
                categoryarray: receptors,
                automargin: true,
            },
        };

        Plotly.newPlot(divElement, [trace], layout, {
            displaylogo: false,
            responsive: true,
            scrollZoom: true,
        });

        observeResize(divElement, () => {
            safePlotResize(divElement);
        });
        window.addEventListener("resize", () => {
            safePlotResize(divElement);
        });
    }

    function renderLigandReceptorStrengthToDiv(cellChatData, divElement) {
        if (!cellChatData || !divElement || !cellChatData.top_interactions)
            return;
        d3.select(divElement).selectAll("*").remove();

        const topInteractions = cellChatData.top_interactions.slice(0, 10);
        if (topInteractions.length === 0) return;

        const ligands = [...new Set(topInteractions.map((d) => d.ligand))];
        const receptors = [...new Set(topInteractions.map((d) => d.receptor))];
        const allNodes = [...ligands, ...receptors];
        const n = allNodes.length;
        const matrix = Array.from({ length: n }, () => Array(n).fill(0));

        topInteractions.forEach((interaction) => {
            const ligandIdx = allNodes.indexOf(interaction.ligand);
            const receptorIdx = allNodes.indexOf(interaction.receptor);
            if (ligandIdx !== -1 && receptorIdx !== -1) {
                matrix[ligandIdx][receptorIdx] =
                    interaction.interaction_score || 0;
            }
        });

        const width = 350;
        const height = 350;
        const innerRadius = Math.min(width, height) * 0.4;
        const outerRadius = innerRadius + 6;

        const chord = d3
            .chordDirected()
            .padAngle(12 / innerRadius)
            .sortSubgroups(d3.descending)
            .sortChords(d3.descending);
        const chords = chord(matrix);
        chords.groups.forEach((d) => {
            d.name = allNodes[d.index];
        });
        chords.forEach((d) => {
            d.source.name = allNodes[d.source.index];
            d.target.name = allNodes[d.target.index];
        });

        const groupScores = matrix.map(
            (row, i) =>
                row.reduce((a, b) => a + b, 0) +
                matrix.reduce((a, b) => a + b[i], 0),
        );
        const radiusScale = d3
            .scaleSqrt()
            .domain([0, d3.max(groupScores)])
            .range([innerRadius, innerRadius + 15]);
        const arc = d3
            .arc()
            .innerRadius(innerRadius)
            .outerRadius((d) => radiusScale(groupScores[d.index]));
        const ribbon = d3
            .ribbonArrow()
            .radius(innerRadius - 0.5)
            .padAngle(1 / innerRadius);

        const padding = 20;
        const svg = d3
            .select(divElement)
            .append("svg")
            .attr("width", width)
            .attr("height", height)
            .attr("viewBox", [
                -(width / 2 + padding),
                -(height / 2 + padding),
                width + 2 * padding,
                height + 2 * padding,
            ])
            .attr(
                "style",
                "width: 100%; height: 100%; font: 10px sans-serif; overflow: visible;",
            );

        const textId = "text-path-id-lr-str-" + (divElement.id || Date.now());
        svg.append("path")
            .attr("id", textId)
            .attr("fill", "none")
            .attr(
                "d",
                d3.arc()({ outerRadius, startAngle: 0, endAngle: 2 * Math.PI }),
            );

        const colorScale = d3
            .scaleOrdinal()
            .domain(allNodes)
            .range(
                allNodes.map((node) => {
                    if (ligands.includes(node)) {
                        return d3.schemeBlues[9][6];
                    } else {
                        return d3.schemeReds[9][6];
                    }
                }),
            );

        svg.append("g")
            .attr("fill-opacity", 0.75)
            .selectAll("path")
            .data(chords)
            .join("path")
            .attr("d", ribbon)
            .attr("fill", (d) => colorScale(allNodes[d.source.index]))
            .style("mix-blend-mode", "multiply")
            .append("title")
            .text((d) => {
                const sourceName = allNodes[d.source.index];
                const targetName = allNodes[d.target.index];
                const value = matrix[d.source.index][d.target.index];
                return `${sourceName} → ${targetName}\nStrength: ${value.toFixed(2)}`;
            });

        const g = svg.append("g").selectAll("g").data(chords.groups).join("g");
        g.append("path")
            .attr("d", arc)
            .attr("fill", (d) => colorScale(allNodes[d.index]))
            .attr("stroke", "#fff");
        g.append("text")
            .attr("dy", -3)
            .append("textPath")
            .attr("xlink:href", `#${textId}`)
            .attr("startOffset", (d) => d.startAngle * outerRadius)
            .text((d) => allNodes[d.index]);
    }

    async function renderDEGHeatmapToDiv(data, divElement) {
        if (!data || !divElement || !data.genes) return;
        Plotly.purge(divElement);

        // Wait for DOM to be ready
        await tick();

        const genes = data.genes;
        const barcodes = data.barcodes || [];
        const expression = data.expression || [];
        const clusters = data.clusters || [];

        if (genes.length === 0 || barcodes.length === 0) return;

        // Build cluster spans
        const clusterSpans = [];
        let prev = clusters[0];
        let start = 0;
        for (let i = 1; i <= clusters.length; i++) {
            if (clusters[i] !== prev) {
                clusterSpans.push({ cluster: prev, start, end: i - 1 });
                prev = clusters[i];
                start = i;
            }
        }

        const customdata = genes.map((gene, i) =>
            barcodes.map((barcode, j) => ({
                barcode,
                cluster: clusters[j],
                gene,
                expression: expression[i] ? expression[i][j] : 0,
            })),
        );

        const heatmap = {
            z: expression,
            x: barcodes,
            y: genes,
            type: "heatmap",
            colorscale: "RdBu",
            zmin: -2,
            zmax: 2,
            showscale: true,
            xgap: 0,
            ygap: 0,
            customdata,
            hovertemplate:
                "Barcode: %{customdata.barcode}<br>" +
                "Cluster: %{customdata.cluster}<br>" +
                "Gene: %{customdata.gene}<br>" +
                "Expression: %{z:.2f}<extra></extra>",
            colorbar: {
                orientation: "h",
                x: 0.5,
                y: -0.1,
                xanchor: "center",
                yanchor: "top",
                len: 0.6,
                thickness: 20,
                title: "Expression",
            },
        };

        const shapes = clusterSpans.map((span) => ({
            type: "rect",
            xref: "x",
            yref: "paper",
            x0: barcodes[span.start],
            x1: barcodes[span.end],
            y0: 1.01,
            y1: 1.05,
            fillcolor: clusterColorScale
                ? clusterColorScale(span.cluster)
                : "#ccc",
            line: { width: 0 },
        }));

        const vlines = clusterSpans.slice(1).map((span) => ({
            type: "line",
            xref: "x",
            yref: "paper",
            x0: barcodes[span.start],
            x1: barcodes[span.start],
            y0: 0,
            y1: 1,
            line: { color: "#888", width: 2 },
        }));

        const layout = {
            title: "DEG Heatmap",
            margin: { t: 50, l: 80, r: 10, b: 10 },
            // Let Plotly autosize to fill the container
            autosize: true,
            height: undefined,
            width: undefined,
            xaxis: {
                tickangle: 90,
                side: "bottom",
                showticklabels: false,
            },
            yaxis: {
                autorange: "reversed",
            },
            shapes: [...shapes, ...vlines],
            showlegend: false,
        };

        Plotly.newPlot(divElement, [heatmap], layout, {
            responsive: true,
            useResizeHandler: true,
            displayModeBar: false,
        });

        // Add resize observer for container size changes
        observeResize(divElement, () => {
            if (divElement && divElement.isConnected) {
                Plotly.Plots.resize(divElement);
            }
        });

        // Also listen to window resize events
        window.addEventListener("resize", () => {
            if (divElement && divElement.isConnected) {
                Plotly.Plots.resize(divElement);
            }
        });
    }

    async function renderDEGDotplotToDiv(data, divElement) {
        if (!data || !divElement || !data.genes) return;
        Plotly.purge(divElement);

        // Wait for DOM to be ready
        await tick();

        const { genes, clusters, data: dotData } = data;
        if (!genes || !clusters || !dotData) return;

        const avgMatrix = genes.map((g) =>
            clusters.map((cl) => {
                const entry = dotData.find(
                    (d) => d.gene === g && d.cluster === cl,
                );
                return entry ? entry.avg_expr : 0;
            }),
        );
        const pctMatrix = genes.map((g) =>
            clusters.map((cl) => {
                const entry = dotData.find(
                    (d) => d.gene === g && d.cluster === cl,
                );
                return entry ? entry.pct_expr * 100 : 0;
            }),
        );

        const x = [];
        const y = [];
        const avgFlat = [];
        const pctFlat = [];
        for (let i = 0; i < genes.length; i++) {
            for (let j = 0; j < clusters.length; j++) {
                x.push(clusters[j]);
                y.push(genes[i]);
                avgFlat.push(avgMatrix[i][j]);
                pctFlat.push(pctMatrix[i][j]);
            }
        }

        const layout = {
            title: "DEG Dotplot",
            xaxis: {
                title: "Cluster",
                tickvals: clusters,
                ticktext: clusters,
                showgrid: false,
            },
            yaxis: {
                title: "",
                tickvals: genes,
                ticktext: genes,
                autorange: "reversed",
                showgrid: true,
                gridcolor: "#eee",
            },
            autosize: true,
            height: undefined,
            width: undefined,
            margin: { t: 50, b: 20, l: 80, r: 10 },
            showlegend: false,
        };

        const trace = {
            type: "scatter",
            mode: "markers",
            x: x,
            y: y,
            marker: {
                size: pctFlat,
                sizemode: "area",
                sizeref: (2.0 * Math.max(...pctFlat, 1)) / 20 ** 2,
                sizemin: 2,
                color: avgFlat,
                colorscale: "PuBuGn",
                colorbar: {
                    title: "Avg Expr",
                    x: 1.02,
                    len: 0.6,
                    thickness: 20,
                },
                line: { width: 0 },
            },
            hovertemplate:
                "<b>%{y}</b> – %{x}<br>Avg Expr: %{marker.color:.2f}<br>Percent: %{marker.size:.1f}%<extra></extra>",
        };

        Plotly.newPlot(divElement, [trace], layout, {
            responsive: true,
            useResizeHandler: true,
            displayModeBar: false,
        });

        // Add resize observer for container size changes
        observeResize(divElement, () => {
            if (divElement && divElement.isConnected) {
                Plotly.Plots.resize(divElement);
            }
        });

        // Also listen to window resize events
        window.addEventListener("resize", () => {
            if (divElement && divElement.isConnected) {
                Plotly.Plots.resize(divElement);
            }
        });
    }

    // Deconvolution rendering functions moved to DeconvolutionAnalysis component
</script>

{#if loading || isLoadingClusterResult || isLoadingData}
    <div
        class="fixed inset-0 z-50 flex justify-center items-center bg-white/80"
    >
        <ProgressRing
            value={null}
            size="size-14"
            meterStroke="stroke-blue-300"
            trackStroke="stroke-blue-400"
        />
        {#if isLoadingClusterResult || isLoadingData}
            <span class="ml-3 text-sm text-gray-600">
                {isLoadingClusterResult
                    ? "Loading cluster result..."
                    : "Loading data..."}
            </span>
        {/if}
    </div>
{/if}

<div class="h-full w-full flex overflow-hidden bg-gray-50">
    <!-- Left column: Cluster result info with spatial preview (fixed on the left, constant width) -->
    <div
        class="h-full flex-shrink-0 border-r border-stone-200 bg-gray-50 p-4 w-[420px]"
    >
        {#if currentClusterResult}
            <div
                class="bg-white border border-stone-300 rounded-lg p-4 w-full sticky top-4"
            >
                <h3 class="font-semibold text-sm text-gray-800 mb-2">
                    Cluster Result Info
                </h3>
                <!-- Text info on top of image - vertical layout -->
                <div class="mb-2 flex flex-col gap-1 text-xs text-gray-700">
                    <div class="flex items-center gap-1.5">
                        <span class="font-semibold text-slate-600">ID:</span>
                        <span class="font-mono text-[11px] text-slate-800">
                            {currentClusterResult.cluster_result_id}
                        </span>
                    </div>
                    <div class="flex items-center gap-1.5">
                        <span class="font-semibold text-slate-600">Method:</span
                        >
                        <span>{currentClusterResult.method || "N/A"}</span>
                    </div>
                    <div class="flex items-center gap-1.5">
                        <span class="font-semibold text-slate-600">k:</span>
                        <span>{currentClusterResult.n_clusters ?? "-"}</span>
                    </div>
                    {#if currentClusterResult.epoch}
                        <div class="flex items-center gap-1.5">
                            <span class="font-semibold text-slate-600"
                                >Epoch:</span
                            >
                            <span>{currentClusterResult.epoch}</span>
                        </div>
                    {/if}
                    {#if selectedClusterResultMeta && (selectedClusterResultMeta.updated_at || selectedClusterResultMeta.created_at)}
                        <div class="flex items-center gap-1.5">
                            <span class="font-semibold text-slate-600"
                                >Date:</span
                            >
                            <span class="text-slate-500">
                                {new Date(
                                    selectedClusterResultMeta.updated_at ||
                                        selectedClusterResultMeta.created_at,
                                ).toLocaleString()}
                            </span>
                        </div>
                    {/if}
                </div>
                <!-- Spatial preview - square and larger -->
                <div
                    class="w-full h-96 border border-stone-200 rounded-lg p-1 bg-gray-50"
                >
                    {#if image && spatialData && spatialData.length > 0}
                        <div
                            style="width: 100%; height: 100%; position: relative;"
                        >
                            <DownstreamPlot
                                bind:this={downstreamPlotInstance}
                                bind:showAttentionFlow
                                {spatialData}
                                {image}
                                {clusterColorScale}
                                {clusterColorScaleVersion}
                                {baseApi}
                                {currentSlice}
                                {hoveredBarcode}
                                on:plotRendered={() => {
                                    // Plot has been rendered with colors, can cancel loading
                                    if (isLoadingData) {
                                        isLoadingData = false;
                                    }
                                }}
                                on:regionsSelected={(e) => {
                                    selectedRegionCount =
                                        e.detail.regions?.length || 0;
                                    selectedBarcodes =
                                        e.detail.allBarcodes || [];
                                    // Reload centers if neighborhood analysis is selected
                                    if (
                                        selectedAnalysis === "neighborhood" &&
                                        currentSlice === "hbrc"
                                    ) {
                                        loadAvailableCenters(
                                            selectedBarcodes.length > 0
                                                ? selectedBarcodes
                                                : null,
                                        );
                                    }
                                }}
                            />
                            {#if currentSlice === "hbrc"}
                                <div
                                    class="absolute top-2 right-2 z-10"
                                    style="display: none;"
                                >
                                    <button
                                        class="px-3 py-1.5 text-xs font-medium rounded-md border transition-colors
                                                    {showAttentionFlow
                                            ? 'bg-blue-500 text-white border-blue-600 hover:bg-blue-600'
                                            : 'bg-white text-gray-700 border-gray-300 hover:bg-gray-50'}"
                                        on:click={() => {
                                            if (downstreamPlotInstance) {
                                                downstreamPlotInstance.toggleAttentionFlow();
                                            }
                                        }}
                                        title="Toggle Attention Flow Vectors (Top 50 vectors shown for performance)"
                                    >
                                        {showAttentionFlow ? "Hide" : "Show"} Attention
                                        Flow
                                    </button>
                                </div>
                            {/if}
                        </div>
                    {:else}
                        <div
                            class="flex items-center justify-center h-full text-gray-500"
                        >
                            <div class="text-center">
                                <p>Loading cluster visualization...</p>
                            </div>
                        </div>
                    {/if}
                </div>
            </div>
        {/if}
    </div>

    <!-- Right column: Analysis results area, scrollable when content exceeds viewport -->
    <div class="flex-1 h-full overflow-y-auto p-4 relative">
        <!-- Analysis cards in a responsive 2-column grid with equal-height cards -->
        <div class="analysis-grid min-w-0">
            {#each loadedAnalyses as analysis (analysis.id)}
                <div
                    class="analysis-card bg-white border border-gray-300 rounded-lg p-4 flex flex-col {analysis.type === 'deconvolution' ? 'col-span-2' : ''}"
                >
                    <div class="flex items-center justify-between mb-2">
                        <div class="flex items-center gap-2 flex-1">
                            <h3 class="text-base font-semibold text-gray-800">
                                {analysis.name}
                                {#if analysis.type === "svg" && analysis.selectedCluster}
                                    <span
                                        class="text-sm font-normal text-gray-600"
                                        >(Cluster {analysis.selectedCluster})</span
                                    >
                                {/if}
                            </h3>
                            {#if analysis.type === "svg" || analysis.type === "deg"}
                                {@const currentMode =
                                    analysis.currentMode ||
                                    (analysis.type === "svg"
                                        ? "Bar"
                                        : "Heatmap")}
                                <div
                                    class="flex gap-1 border border-gray-200 rounded"
                                >
                                    {#if analysis.type === "svg"}
                                        <button
                                            type="button"
                                            class="px-2 py-0.5 text-xs rounded transition-colors {currentMode ===
                                            'Bar'
                                                ? 'bg-gray-200 text-gray-800'
                                                : 'text-gray-600 hover:bg-gray-100'}"
                                            on:click={() => {
                                                const index =
                                                    loadedAnalyses.findIndex(
                                                        (a) =>
                                                            a.id ===
                                                            analysis.id,
                                                    );
                                                if (index !== -1) {
                                                    loadedAnalyses[index] = {
                                                        ...loadedAnalyses[
                                                            index
                                                        ],
                                                        currentMode: "Bar",
                                                    };
                                                    loadedAnalyses =
                                                        loadedAnalyses;
                                                    // Let reactive block + ResizeObserver handle sizing
                                                }
                                            }}
                                        >
                                            Enrichment
                                        </button>
                                        <button
                                            type="button"
                                            class="px-2 py-0.5 text-xs rounded transition-colors {currentMode ===
                                            'Sankey'
                                                ? 'bg-gray-200 text-gray-800'
                                                : 'text-gray-600 hover:bg-gray-100'}"
                                            on:click={() => {
                                                const index =
                                                    loadedAnalyses.findIndex(
                                                        (a) =>
                                                            a.id ===
                                                            analysis.id,
                                                    );
                                                if (index !== -1) {
                                                    loadedAnalyses[index] = {
                                                        ...loadedAnalyses[
                                                            index
                                                        ],
                                                        currentMode: "Sankey",
                                                    };
                                                    loadedAnalyses =
                                                        loadedAnalyses;
                                                    // Let reactive block + ResizeObserver handle sizing
                                                }
                                            }}
                                        >
                                            Gene–Pathway
                                        </button>
                                    {:else if analysis.type === "deg"}
                                        <button
                                            type="button"
                                            class="px-2 py-0.5 text-xs rounded transition-colors {currentMode ===
                                            'Heatmap'
                                                ? 'bg-gray-200 text-gray-800'
                                                : 'text-gray-600 hover:bg-gray-100'}"
                                            on:click={() => {
                                                const index =
                                                    loadedAnalyses.findIndex(
                                                        (a) =>
                                                            a.id ===
                                                            analysis.id,
                                                    );
                                                if (index !== -1) {
                                                    loadedAnalyses[index] = {
                                                        ...loadedAnalyses[
                                                            index
                                                        ],
                                                        currentMode: "Heatmap",
                                                    };
                                                    loadedAnalyses =
                                                        loadedAnalyses;
                                                }
                                            }}
                                        >
                                            Heatmap
                                        </button>
                                        <button
                                            type="button"
                                            class="px-2 py-0.5 text-xs rounded transition-colors {currentMode ===
                                            'Dotplot'
                                                ? 'bg-gray-200 text-gray-800'
                                                : 'text-gray-600 hover:bg-gray-100'}"
                                            on:click={() => {
                                                const index =
                                                    loadedAnalyses.findIndex(
                                                        (a) =>
                                                            a.id ===
                                                            analysis.id,
                                                    );
                                                if (index !== -1) {
                                                    loadedAnalyses[index] = {
                                                        ...loadedAnalyses[
                                                            index
                                                        ],
                                                        currentMode: "Dotplot",
                                                    };
                                                    loadedAnalyses =
                                                        loadedAnalyses;
                                                }
                                            }}
                                        >
                                            Dotplot
                                        </button>
                                    {/if}
                                </div>
                            {:else if analysis.type === "spatial"}
                                {@const spatialMode =
                                    analysis.currentMode || "Cluster"}
                                <div
                                    class="flex gap-1 border border-gray-200 rounded"
                                >
                                    <button
                                        type="button"
                                        class="px-2 py-0.5 text-xs rounded transition-colors {spatialMode ===
                                        'Cluster'
                                            ? 'bg-gray-200 text-gray-800'
                                            : 'text-gray-600 hover:bg-gray-100'}"
                                        on:click={() => {
                                            const index =
                                                loadedAnalyses.findIndex(
                                                    (a) => a.id === analysis.id,
                                                );
                                            if (index !== -1) {
                                                loadedAnalyses[index] = {
                                                    ...loadedAnalyses[index],
                                                    currentMode: "Cluster",
                                                };
                                                loadedAnalyses = loadedAnalyses;
                                            }
                                        }}
                                    >
                                        Cluster
                                    </button>
                                    <button
                                        type="button"
                                        class="px-2 py-0.5 text-xs rounded transition-colors {spatialMode ===
                                        'Ligand-Receptor'
                                            ? 'bg-gray-200 text-gray-800'
                                            : 'text-gray-600 hover:bg-gray-100'}"
                                        on:click={() => {
                                            const index =
                                                loadedAnalyses.findIndex(
                                                    (a) => a.id === analysis.id,
                                                );
                                            if (index !== -1) {
                                                loadedAnalyses[index] = {
                                                    ...loadedAnalyses[index],
                                                    currentMode:
                                                        "Ligand-Receptor",
                                                };
                                                loadedAnalyses = loadedAnalyses;
                                            }
                                        }}
                                    >
                                        Ligand-Receptor
                                    </button>
                                </div>
                            {/if}
                        </div>
                        <div class="flex items-center gap-2">
                            <button
                                type="button"
                                class="text-blue-500 hover:text-blue-700 disabled:text-gray-400 disabled:cursor-not-allowed"
                                on:click={() => analyzeWithKimi(analysis.id)}
                                disabled={analysis.kimiLoading || !analysis.data}
                                title="Analyze with AI assistant"
                                aria-label="Analyze with AI assistant"
                            >
                                {#if analysis.kimiLoading}
                                    <svg
                                        class="w-4 h-4 animate-spin"
                                        fill="none"
                                        stroke="currentColor"
                                        viewBox="0 0 24 24"
                                    >
                                        <path
                                            stroke-linecap="round"
                                            stroke-linejoin="round"
                                            stroke-width="2"
                                            d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
                                        />
                                    </svg>
                                {:else}
                                    <svg
                                        class="w-4 h-4"
                                        fill="none"
                                        stroke="currentColor"
                                        viewBox="0 0 24 24"
                                    >
                                        <path
                                            stroke-linecap="round"
                                            stroke-linejoin="round"
                                            stroke-width="2"
                                            d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z"
                                        />
                                    </svg>
                                {/if}
                            </button>
                            <button
                                type="button"
                                class="text-gray-400 hover:text-gray-600"
                                on:click={() => removeAnalysis(analysis.id)}
                                title="Remove analysis"
                                aria-label="Remove analysis"
                            >
                                <svg
                                    class="w-4 h-4"
                                    fill="none"
                                    stroke="currentColor"
                                    viewBox="0 0 24 24"
                                >
                                    <path
                                        stroke-linecap="round"
                                        stroke-linejoin="round"
                                        stroke-width="2"
                                        d="M6 18L18 6M6 6l12 12"
                                    />
                                </svg>
                            </button>
                        </div>
                    </div>
                    <p class="text-xs text-gray-600 mb-3">
                        {analysis.description}
                    </p>
                    {#if analysis.loading}
                        <div class="flex items-center justify-center py-8">
                            <ProgressRing value={null} size="size-8" />
                            <span class="ml-2 text-xs text-gray-600"
                                >Loading...</span
                            >
                        </div>
                    {:else if analysis.error}
                        <div class="text-xs text-red-600 py-4">
                            {analysis.error}
                        </div>
                    {:else if analysis.needsClusterSelection}
                        <div class="text-xs text-gray-500 py-4">
                            Please select a cluster in the dialog
                        </div>
                    {:else if analysis.needsRegionSelection}
                        <div class="text-xs text-gray-500 py-4">
                            Please select a region first
                        </div>
                    {:else if analysis.data}
                        <!-- Render analysis content based on type -->
                        <div class="flex-1 flex flex-col min-h-0">
                            {#if analysis.type === "svg"}
                                <!-- SVG Analysis: Bar and Sankey -->
                                {@const svgMode = analysis.currentMode || "Bar"}
                                <!-- Always render both divs, but show/hide based on mode -->
                                <div
                                    class="w-full flex-1 relative"
                                    style="min-height: 384px;"
                                >
                                    <div
                                        bind:this={analysis.chartDiv}
                                        class="w-full h-full"
                                        class:hidden={svgMode !== "Bar"}
                                    ></div>
                                    <div
                                        bind:this={analysis.chartDiv2}
                                        class="w-full h-full"
                                        class:hidden={svgMode !== "Sankey"}
                                    ></div>
                                </div>
                            {:else if analysis.type === "spatial"}
                                <!-- Spatial Communication: Cluster-level bubble plot + Ligand-Receptor chord diagrams -->
                                {@const spatialMode =
                                    analysis.currentMode || "Cluster"}
                                <div class="w-full">
                                    <!-- Cluster mode -->
                                    <div
                                        class="flex flex-col flex-1"
                                        style="min-height: 360px;"
                                        class:hidden={spatialMode !== "Cluster"}
                                    >
                                        <div
                                            class="text-xs font-semibold text-gray-700 mb-2"
                                        >
                                            Cluster communication (size =
                                            Number, color = Strength)
                                        </div>
                                        <div
                                            class="w-full flex-1"
                                            style="min-height: 0;"
                                        >
                                            <div
                                                bind:this={analysis.chartDiv3}
                                                class="w-full h-full"
                                            ></div>
                                        </div>
                                    </div>

                                    <!-- Ligand-Receptor mode -->
                                    <div
                                        class="flex flex-col flex-1"
                                        style="min-height: 360px;"
                                        class:hidden={spatialMode !==
                                            "Ligand-Receptor"}
                                    >
                                        <div
                                            class="text-xs font-semibold text-gray-700 mb-2"
                                        >
                                            Ligand–Receptor communication (size
                                            = Count, color = Strength)
                                        </div>
                                        <div
                                            class="w-full flex-1"
                                            style="min-height: 0;"
                                        >
                                            <div
                                                bind:this={analysis.chartDiv5}
                                                class="w-full h-full"
                                            ></div>
                                        </div>
                                    </div>
                                </div>
                            {:else if analysis.type === "deg"}
                                <!-- DEG Analysis: Heatmap and Dotplot -->
                                {@const degMode =
                                    analysis.currentMode || "Heatmap"}
                                <!-- Always render both divs, but show/hide based on mode -->
                                <div
                                    class="w-full flex-1 relative"
                                    style="min-height: 384px;"
                                >
                                    <div
                                        bind:this={analysis.chartDiv}
                                        class="w-full h-full"
                                        class:hidden={degMode !== "Heatmap"}
                                    ></div>
                                    <div
                                        bind:this={analysis.chartDiv2}
                                        class="w-full h-full"
                                        class:hidden={degMode !== "Dotplot"}
                                    ></div>
                                </div>
                            {:else if analysis.type === "deconvolution"}
                                <!-- Deconvolution Analysis Component -->
                                <DeconvolutionAnalysis
                                    data={analysis.data}
                                    selectedCellTypes={selectedCellTypes}
                                    spatialData={spatialData}
                                    on:cellTypesChange={(e) => {
                                        selectedCellTypes = e.detail;
                                    }}
                                />
                            {:else}
                                <!-- Other analyses: single chart -->
                                <div
                                    class="w-full flex-1"
                                    style="min-height: 384px;"
                                    bind:this={analysis.chartDiv}
                                ></div>
                            {/if}
                        </div>
                        <!-- AI Assistant Analysis Results -->
                        {#if analysis.kimiAnalysis || analysis.kimiError}
                            <div class="mt-4 pt-4 border-t border-gray-200">
                                <div class="flex items-center gap-2 mb-2">
                                    <svg
                                        class="w-4 h-4 text-blue-500"
                                        fill="none"
                                        stroke="currentColor"
                                        viewBox="0 0 24 24"
                                    >
                                        <path
                                            stroke-linecap="round"
                                            stroke-linejoin="round"
                                            stroke-width="2"
                                            d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z"
                                        />
                                    </svg>
                                    <h4 class="text-sm font-semibold text-gray-800">
                                        AI Assistant Analysis
                                    </h4>
                                </div>
                                {#if analysis.kimiError}
                                    <div class="text-xs text-red-600 bg-red-50 p-2 rounded">
                                        Error: {analysis.kimiError}
                                    </div>
                                {:else if analysis.kimiAnalysis}
                                    <div class="text-xs text-gray-700 bg-blue-50 p-3 rounded max-h-64 overflow-y-auto">
                                        <div class="markdown-content prose prose-sm max-w-none prose-headings:mt-2 prose-headings:mb-1 prose-p:my-1 prose-ul:my-1 prose-ol:my-1 prose-li:my-0.5">
                                            {@html markdownToHtml(analysis.kimiAnalysis)}
                                        </div>
                                    </div>
                                {/if}
                            </div>
                        {/if}
                    {/if}
                </div>
            {/each}

            {#if loadedAnalyses.length === 0}
                <div
                    class="absolute inset-0 flex items-center justify-center text-gray-500"
                >
                    <div class="text-center">
                        <p class="text-sm">No analyses loaded yet.</p>
                        <p class="text-xs mt-1">
                            Click the + button to add an analysis.
                        </p>
                    </div>
                </div>
            {/if}
        </div>
    </div>

    <!-- Analysis Selection Dialog -->
    {#if showAnalysisDialog}
        <div
            class="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
            on:click={() => {
                showAnalysisDialog = false;
                pendingSVGAnalysis = false;
                selectedSVGClusterInDialog = null;
            }}
        >
            <div
                class="bg-white rounded-lg shadow-xl max-w-2xl w-full mx-4 max-h-[80vh] overflow-auto"
                on:click|stopPropagation
            >
                <div class="p-6">
                    <div class="flex items-center justify-between mb-4">
                        <h2 class="text-xl font-semibold text-gray-800">
                            Select Analysis Type
                        </h2>
                        <button
                            type="button"
                            class="text-gray-400 hover:text-gray-600"
                            on:click={() => {
                                showAnalysisDialog = false;
                                pendingSVGAnalysis = false;
                                selectedSVGClusterInDialog = null;
                            }}
                        >
                            <svg
                                class="w-6 h-6"
                                fill="none"
                                stroke="currentColor"
                                viewBox="0 0 24 24"
                            >
                                <path
                                    stroke-linecap="round"
                                    stroke-linejoin="round"
                                    stroke-width="2"
                                    d="M6 18L18 6M6 6l12 12"
                                />
                            </svg>
                        </button>
                    </div>
                    {#if pendingSVGAnalysis}
                        <!-- SVG Analysis: Cluster Selection -->
                        <div class="space-y-4">
                            <div>
                                <h3
                                    class="text-base font-semibold text-gray-800 mb-2"
                                >
                                    SVG Analysis
                                </h3>
                                <p class="text-sm text-gray-600 mb-4">
                                    {analysisTypes.find((a) => a.id === "svg")
                                        ?.description}
                                </p>
                            </div>
                            <div class="flex flex-col space-y-2">
                                <label
                                    for="svg-cluster-select-dialog"
                                    class="text-sm font-medium text-gray-700"
                                >
                                    Select Cluster:
                                </label>
                                <select
                                    id="svg-cluster-select-dialog"
                                    class="border border-gray-300 rounded px-3 py-2 bg-white text-sm focus:ring-2 focus:ring-stone-400"
                                    bind:value={selectedSVGClusterInDialog}
                                >
                                    <option value=""
                                        >-- Select Cluster --</option
                                    >
                                    {#each availableClusters as c}
                                        {@const used =
                                            svgClustersInUse &&
                                            svgClustersInUse.includes(`${c}`)}
                                        <option value={c} disabled={used}>
                                            {#if used}
                                                {c} (already added)
                                            {:else}
                                                {c}
                                            {/if}
                                        </option>
                                    {/each}
                                </select>
                            </div>
                            <div class="flex gap-2 justify-end">
                                <button
                                    type="button"
                                    class="px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-md hover:bg-gray-50"
                                    on:click={() => {
                                        pendingSVGAnalysis = false;
                                        selectedSVGClusterInDialog = null;
                                    }}
                                >
                                    Cancel
                                </button>
                                <button
                                    type="button"
                                    class="px-4 py-2 text-sm font-medium text-white bg-gray-700 rounded-md hover:bg-gray-800 disabled:bg-gray-300 disabled:cursor-not-allowed"
                                    disabled={!selectedSVGClusterInDialog}
                                    on:click={() => {
                                        if (selectedSVGClusterInDialog) {
                                            addAnalysis(
                                                "svg",
                                                selectedSVGClusterInDialog,
                                            );
                                        }
                                    }}
                                >
                                    Add Analysis
                                </button>
                            </div>
                        </div>
                    {:else}
                        <div class="grid grid-cols-1 gap-3">
                            {#each analysisTypes as analysis}
                                {#if (selectedRegionCount === 0 && analysis.id !== "neighborhood") || (selectedRegionCount > 0 && analysis.id === "neighborhood")}
                                    {#if analysis.id === "svg"}
                                        <!-- SVG analysis: disable when all clusters already have SVG cards -->
                                        <button
                                            class="group w-full bg-white rounded-lg border border-gray-200 px-5 py-4 hover:border-gray-400 hover:shadow-md transition-all duration-200 cursor-pointer text-left disabled:opacity-50 disabled:cursor-not-allowed"
                                            on:click={() =>
                                                addAnalysis(analysis.id)}
                                            disabled={svgAllClustersUsed}
                                        >
                                            <h3
                                                class="text-base font-semibold text-gray-800 group-hover:text-gray-700 mb-1"
                                            >
                                                {analysis.name}
                                            </h3>
                                            <p class="text-sm text-gray-600">
                                                {analysis.description}
                                            </p>
                                            {#if svgAllClustersUsed}
                                                <p
                                                    class="text-xs text-gray-500 mt-1"
                                                >
                                                    All clusters already have
                                                    SVG analyses.
                                                </p>
                                            {/if}
                                        </button>
                                    {:else}
                                        <button
                                            class="group w-full bg-white rounded-lg border border-gray-200 px-5 py-4 hover:border-gray-400 hover:shadow-md transition-all duration-200 cursor-pointer text-left disabled:opacity-50 disabled:cursor-not-allowed"
                                            on:click={() =>
                                                addAnalysis(analysis.id)}
                                            disabled={loadedAnalyses.find(
                                                (a) =>
                                                    a.type === analysis.id &&
                                                    (!analysis.selectedCluster ||
                                                        a.selectedCluster ===
                                                            analysis.selectedCluster),
                                            ) !== undefined}
                                        >
                                            <h3
                                                class="text-base font-semibold text-gray-800 group-hover:text-gray-700 mb-1"
                                            >
                                                {analysis.name}
                                            </h3>
                                            <p class="text-sm text-gray-600">
                                                {analysis.description}
                                            </p>
                                            {#if loadedAnalyses.find((a) => a.type === analysis.id)}
                                                <p
                                                    class="text-xs text-gray-500 mt-1"
                                                >
                                                    Already loaded
                                                </p>
                                            {/if}
                                        </button>
                                    {/if}
                                {/if}
                            {/each}
                        </div>
                    {/if}
                </div>
            </div>
        </div>
    {/if}
</div>

<style>
    /* Custom styles for the downstream analysis component */

    /* Ensure Plotly tooltips appear above canvas overlay for deconvolution charts */
    :global(.plotly .infolayer) {
        z-index: 9999 !important;
        pointer-events: none !important;
    }

    :global(.plotly .hoverlayer) {
        z-index: 10000 !important;
        pointer-events: auto !important;
    }

    /* Ensure Plotly's main plot container is above canvas */
    :global(.plotly) {
        position: relative;
        z-index: 1;
    }

    /* Make sure Plotly's SVG layers are above canvas */
    :global(.plotly svg) {
        position: relative;
        z-index: 2;
    }

    /* Analysis grid: 2 cards per row, auto-wrap, equal row heights */
    .analysis-grid {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 1rem; /* matches Tailwind gap-4 */
        align-items: stretch;
    }

    /* Ensure analysis cards have the same overall height and stretch with the grid row */
    .analysis-card {
        min-height: 552px;
        height: 100%;
    }

    /* Fallback for very narrow viewports: stack cards in one column */
    @media (max-width: 1024px) {
        .analysis-grid {
            grid-template-columns: minmax(0, 1fr);
        }
    }
</style>
