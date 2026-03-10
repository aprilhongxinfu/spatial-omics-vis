<script>
    import { createEventDispatcher, onMount, onDestroy, tick } from "svelte";
    import Plotly from "plotly.js-dist-min";
    import * as d3 from "d3";

    let spatialDiv;
    export let spatialData;
    export let image;
    export let clusterColorScale;
    export let clusterColorScaleVersion = 0;
    export let hoveredBarcode;
    export let baseApi;
    export let currentSlice;

    const dispatch = createEventDispatcher();
    let resizeObserver;
    let plotInstance = null;
    let availableClusters = [];
    let currentDragMode = null; // Track current drag mode to restore after redraw
    let colorCache = new Map(); // Cache color lookups to avoid repeated calculations
    let lastColorScaleVersion = -1; // Track color scale version to invalidate cache

    // Multiple region selection support
    let selectedRegions = []; // Array of { id, barcodes, color }
    let regionIdCounter = 0;
    const regionColors = [
        "#ff6b6b", "#4ecdc4", "#45b7d1", "#96ceb4", 
        "#ffeaa7", "#dda15e", "#bc6c25", "#6c5ce7"
    ];

    // Approximate arrow length in data coordinates, scaled to spot size
    // Will be estimated once we know image width and total spot count
    let arrowLengthScale = 80; // fallback default

    // Attention flow vectors support
    let attentionFlowData = [];
    export let showAttentionFlow = false;

    function toBase64(img) {
        const canvas = document.createElement("canvas");
        canvas.width = img.width;
        canvas.height = img.height;
        const ctx = canvas.getContext("2d");
        ctx.drawImage(img, 0, 0);
        return canvas.toDataURL("image/png");
    }

    function getAllSelectedBarcodes() {
        const allBarcodes = new Set();
        selectedRegions.forEach(region => {
            region.barcodes.forEach(bc => allBarcodes.add(bc));
        });
        return Array.from(allBarcodes);
    }

    // Calculate convex hull for irregular region boundary
    function getConvexHull(points) {
        if (points.length < 3) {
            // If less than 3 points, create a small circle around them
            if (points.length === 1) {
                const [x, y] = points[0];
                const radius = 10;
                return Array.from({ length: 16 }, (_, i) => {
                    const angle = (i / 16) * 2 * Math.PI;
                    return [x + radius * Math.cos(angle), y + radius * Math.sin(angle)];
                });
            }
            // For 2 points, create a capsule shape
            const [p1, p2] = points;
            const dx = p2[0] - p1[0];
            const dy = p2[1] - p1[1];
            const dist = Math.sqrt(dx * dx + dy * dy);
            const radius = 5;
            const perpX = -dy / dist * radius;
            const perpY = dx / dist * radius;
            return [
                [p1[0] + perpX, p1[1] + perpY],
                [p2[0] + perpX, p2[1] + perpY],
                [p2[0] - perpX, p2[1] - perpY],
                [p1[0] - perpX, p1[1] - perpY],
            ];
        }

        // Sort points by x, then by y
        const sorted = [...points].sort((a, b) => {
            if (a[0] !== b[0]) return a[0] - b[0];
            return a[1] - b[1];
        });

        // Graham scan for convex hull
        function cross(o, a, b) {
            return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
        }

        // Lower hull
        const lower = [];
        for (let i = 0; i < sorted.length; i++) {
            while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], sorted[i]) <= 0) {
                lower.pop();
            }
            lower.push(sorted[i]);
        }

        // Upper hull
        const upper = [];
        for (let i = sorted.length - 1; i >= 0; i--) {
            while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], sorted[i]) <= 0) {
                upper.pop();
            }
            upper.push(sorted[i]);
        }

        // Remove duplicates and combine
        upper.pop();
        lower.pop();
        return lower.concat(upper);
    }

    function getRegionBoundary(region) {
        // Collect all points in this region across all traces
        const points = [];
        
        spatialData.forEach((trace) => {
            const barcodes = trace.customdata || trace.text || [];
            
            region.barcodes.forEach(barcode => {
                const index = barcodes.indexOf(barcode);
                if (index !== -1 && trace.x && trace.y && 
                    Array.isArray(trace.x) && Array.isArray(trace.y) &&
                    index < trace.x.length && index < trace.y.length) {
                    points.push([trace.x[index], trace.y[index]]);
                }
            });
        });
        
        if (points.length === 0) {
            return null;
        }
        
        // Calculate convex hull for irregular boundary
        const hull = getConvexHull(points);
        
        return hull;
    }

    async function drawPlot() {
        // Only purge if we're doing a full reset (e.g., data structure changed significantly)
        // For color updates, we can use react which is much faster
        const needsFullReset = !plotInstance || !spatialDiv || !spatialDiv._fullData;
        if (needsFullReset && plotInstance && spatialDiv) {
            Plotly.purge(spatialDiv);
            plotInstance = null;
        }
        
        if (!image) {
            console.warn("❌ image not loaded yet");
            return;
        }
        
        if (!spatialDiv || !spatialDiv.isConnected) {
            console.warn("❌ spatialDiv not available yet");
            return;
        }
        
        if (!clusterColorScale || typeof clusterColorScale !== 'function') {
            console.warn("❌ clusterColorScale not initialized yet", {
                clusterColorScale,
                type: typeof clusterColorScale,
                version: clusterColorScaleVersion
            });
            return;
        }
        
        // Debug: Log color scale info
        if (clusterColorScale && typeof clusterColorScale === 'function' && clusterColorScale.domain) {
            const domain = clusterColorScale.domain();
            console.log("🎨 clusterColorScale info:", {
                version: clusterColorScaleVersion,
                domainLength: domain.length,
                domainSample: domain.slice(0, 5),
                hasFunction: typeof clusterColorScale === 'function'
            });
        }

        if (!spatialData || !Array.isArray(spatialData) || spatialData.length === 0) {
            console.warn("❌ spatialData not available or empty", { spatialData });
            return;
        }

        console.log("✅ Drawing plot with spatialData:", {
            tracesCount: spatialData.length,
            firstTrace: spatialData[0] ? {
                name: spatialData[0].name,
                pointsCount: spatialData[0].x?.length || 0
            } : null
        });
        
        const base64 = toBase64(image);
        const layout = {
            autosize: true,
            title: "Spatial Clusters",
            uirevision: 'same', // Preserve zoom/pan state across redraws
            xaxis: {
                visible: false,
                range: [0, image.width],
            },
            yaxis: {
                visible: false,
                range: [image.height, 0],
                scaleanchor: "x",
                scaleratio: 1,
            },
            // Use current drag mode if available, otherwise default to false (pan/zoom)
            dragmode: (currentDragMode && (currentDragMode === 'lasso' || currentDragMode === 'select')) ? currentDragMode : false,
            margin: { l: 0, r: 0, t: 0, b: 0 },
            legend: { x: 0, y: 0, bgcolor: "rgba(255,255,255,0.6)" },
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

        // Normalize cluster name function (same as plot.svelte)
        function normalizeClusterName(name) {
            if (name === null || name === undefined) return "";
            return `${name}`
                .replace(/^cluster_/i, "")
                .trim();
        }

        // Invalidate color cache if color scale version changed
        if (lastColorScaleVersion !== clusterColorScaleVersion) {
            colorCache.clear();
            lastColorScaleVersion = clusterColorScaleVersion;
        }

        // Optimized color lookup function with caching
        function getColorForCluster(traceName) {
            // Check cache first
            if (colorCache.has(traceName)) {
                return colorCache.get(traceName);
            }

            // Normalize trace name
            const normalizedName = normalizeClusterName(traceName);
            const traceNameStr = `${traceName}`.trim();
            const normalizedNameStr = `${normalizedName}`.trim();
            
            // Try variants in order of likelihood (most common first)
            const variantsToTry = [
                normalizedNameStr,              // normalized (trimmed) - most common
                traceNameStr,                  // original (trimmed)
                normalizedName,                // normalized (original)
                traceName,                    // original
                `Cluster ${normalizedNameStr}`, // "Cluster {normalized}"
                `cluster ${normalizedNameStr}`, // "cluster {normalized}"
            ];
            
            // Try numeric variants if applicable
            const numericValue = Number.parseFloat(normalizedNameStr);
            if (!Number.isNaN(numericValue)) {
                variantsToTry.push(
                    `${numericValue}`,
                    `${numericValue.toFixed(0)}`,
                    numericValue
                );
            }
            
            // Try each variant until we find a valid color
            let color = null;
            for (const variant of variantsToTry) {
                const testColor = clusterColorScale(variant);
                if (testColor && testColor !== "#999999") {
                    color = testColor;
                    break;
                }
            }
            
            // Final fallback
            if (!color || color === "#999999") {
                color = clusterColorScale(traceNameStr) || "#999999";
            }

            // Cache the result for future use
            colorCache.set(traceName, color);
            return color;
        }

        // 当显示 attention flow 时，可以隐藏原始 spot 点，突出向量场
        const traces = showAttentionFlow
            ? []
            : spatialData.map((trace) => {
                  const barcodes = trace.customdata || trace.text || [];
                  const hoveredIndex = barcodes.indexOf(
                      hoveredBarcode?.barcode ?? "",
                  );

                  const isHovering =
                      hoveredBarcode?.barcode &&
                      hoveredBarcode?.barcode !== "" &&
                      hoveredBarcode?.barcode !== -1;

                  // Use optimized cached color lookup
                  const color = getColorForCluster(trace.name);

                  // Keep points in original cluster colors, no selection highlighting on points
                  return {
                      ...trace,
                      marker: {
                          ...trace.marker,
                          color: color,
                          opacity: isHovering ? 0.2 : 1,
                      },
                      name: `Cluster ${trace.name}`,
                      selectedpoints:
                          isHovering && hoveredIndex !== -1
                              ? [hoveredIndex]
                              : null,
                      selected: { marker: { opacity: 1 } },
                      unselected: { marker: { opacity: 0.2 } },
                  };
              });
        
        // Create irregular boundary shapes (polygons) for each selected region
        const regionShapes = selectedRegions
            .map(region => {
                const boundary = getRegionBoundary(region);
                if (!boundary || boundary.length < 3) return null;
                
                // Create SVG path string for the polygon
                const path = boundary
                    .map((point, i) => `${i === 0 ? 'M' : 'L'} ${point[0]} ${point[1]}`)
                    .join(' ') + ' Z';
                
                return {
                    type: 'path',
                    xref: 'x',
                    yref: 'y',
                    path: path,
                    fillcolor: 'transparent',
                    line: {
                        color: region.color,
                        width: 3,
                    },
                    layer: 'above',
                };
            })
            .filter(shape => shape !== null);
        
        // Add shapes to layout (always set to array, even if empty)
        layout.shapes = regionShapes;

        // 基于 spot 密度估计一个合适的线段长度（数据坐标系）
        if (image && spatialData && spatialData.length) {
            const totalPoints = spatialData.reduce(
                (sum, trace) =>
                    sum +
                    (Array.isArray(trace?.x) ? trace.x.length : 0),
                0,
            );
            if (totalPoints > 0 && image.width) {
                const approxPerRow = Math.sqrt(totalPoints);
                const approxSpacing = image.width / approxPerRow; // 邻近 spot 之间的大致间距
                // 让 glyph 更紧凑：使用约 0.4 倍的点间距作为线段长度
                arrowLengthScale = approxSpacing * 0.4; // 0.3~0.5 区间都可以微调
            }
        }

        // Add attention flow vector field if enabled
        // 创建类似 Python 截图中的“小花状”向量场
        if (showAttentionFlow && attentionFlowData.length > 0) {
            // 1) 每个 center 取 top-k neighbors，形成三瓣/多瓣小花
            const grouped = d3.group(
                attentionFlowData,
                (d) => d.center_barcode || d.center_name,
            );
            const flowsForVectors = [];
            const TOP_K_PER_CENTER = 3;

            for (const flows of grouped.values()) {
                const topK = [...flows]
                    .sort((a, b) => b.attn_score - a.attn_score)
                    .slice(0, TOP_K_PER_CENTER);
                flowsForVectors.push(...topK);
            }

            if (flowsForVectors.length > 0) {
                // 2) 计算 score 范围，建立 Plasma 颜色映射
                // 使用每个 center 的聚合强度 attn_strength，而不是单条边的 attn_score
                const strengths = flowsForVectors.map(
                    (f) => f.attn_strength ?? f.attn_score,
                );
                const minScore = Math.min(...strengths);
                const maxScore = Math.max(...strengths);

                const colorScale = d3
                    .scaleSequential()
                    .domain([minScore, maxScore])
                    .interpolator(d3.interpolatePlasma); // 紫→橙黄

                // 3) 用 scattergl 只画点，用于 colorbar（颜色由 Plotly 控制）
                const vectorFieldTrace = {
                    type: "scattergl",
                    mode: "markers",
                    x: flowsForVectors.map((f) => f.center_x),
                    y: flowsForVectors.map((f) => f.center_y),
                    marker: {
                        size: 2.5,
                        color: strengths, // 使用 center 的聚合强度
                        colorscale: "Plasma", // 和 d3 保持一致
                        showscale: true,
                        colorbar: {
                            title: "Attention Strength",
                            titleside: "right",
                            thickness: 15,
                            len: 0.6,
                            x: 1.02,
                            y: 0.5,
                            xanchor: "left",
                            yanchor: "middle",
                        },
                        opacity: 0.8,
                    },
                    name: "Attention Flow",
                    showlegend: false,
                    hoverinfo: "skip",
                };
                traces.unshift(vectorFieldTrace);

                // 4) 再用 shapes 画短线段（没有箭头头部）
                const glyphShapes = flowsForVectors.map((flow) => {
                    const dx = flow.neighbor_x - flow.center_x;
                    const dy = flow.neighbor_y - flow.center_y;
                    const length = Math.sqrt(dx * dx + dy * dy) || 1;

                    // 和 spot 间距相关的短线长度（40~60 像素）
                    const fixedLength = arrowLengthScale;
                    const scale = fixedLength / length;

                    const headX = flow.center_x + dx * scale;
                    const headY = flow.center_y + dy * scale;

                    const color = colorScale(flow.attn_strength ?? flow.attn_score);

                    return {
                        type: "line",
                        xref: "x",
                        yref: "y",
                        x0: flow.center_x,
                        y0: flow.center_y,
                        x1: headX,
                        y1: headY,
                        line: {
                            color,
                            width: 1.2, // 细线，更像 glyph
                        },
                        layer: "above",
                    };
                });

                // 把原来的 regionShapes + glyphShapes 合在一起
                layout.shapes = [...(regionShapes || []), ...glyphShapes];
            } else {
                layout.shapes = regionShapes;
            }

            // 不再使用 annotations
            layout.annotations = [];
        } else {
            // 只保留选区边界的 shapes
            layout.shapes = regionShapes;
            layout.annotations = [];
        }

        const clusterSet = new Set();
        spatialData.forEach((trace) => clusterSet.add(trace.name));
        availableClusters = Array.from(clusterSet);

        // Use Plotly.react for smoother updates instead of recreating the plot
        // This avoids the progressive "coloring process" by updating in place
        if (plotInstance && spatialDiv._fullData && spatialDiv._fullData.length > 0) {
            // Update existing plot - much faster and smoother than recreating
            // Plotly.react only updates what changed, avoiding the progressive coloring effect
            await Plotly.react(spatialDiv, traces, layout, {
                displayModeBar: true,
                scrollZoom: true,
                displaylogo: false,
                modeBarButtons: [
                    ["pan2d", "lasso2d", "select2d", "toImage"],
                ],
                responsive: true,
            });
            // Update plotInstance reference
            plotInstance = spatialDiv._fullLayout ? spatialDiv._fullLayout : plotInstance;
            // Wait for plot to render and apply colors
            await waitForPlotRender();
        } else {
            // First time rendering - use newPlot
            plotInstance = await Plotly.newPlot(spatialDiv, traces, layout, {
                displayModeBar: true,
                scrollZoom: true,
                displaylogo: false,
                modeBarButtons: [
                    ["pan2d", "lasso2d", "select2d", "toImage"],
                ],
                responsive: true,
            });
            bindPlotEvents();
            // Wait for plot to render and apply colors
            await waitForPlotRender();
        }
    }

    // Wait for Plotly to finish rendering and applying colors
    function waitForPlotRender() {
        return new Promise((resolve) => {
            if (!spatialDiv) {
                resolve();
                return;
            }
            
            let resolved = false;
            
            // Listen for plotly_afterplot event which fires after plot is fully rendered
            // Plotly events are attached to the plot instance
            const handleAfterPlot = () => {
                if (resolved) return;
                resolved = true;
                // Wait a bit more to ensure colors are fully applied
                setTimeout(() => {
                    dispatch("plotRendered");
                    resolve();
                }, 200);
            };
            
            // Use plotInstance if available (preferred method)
            if (plotInstance && typeof plotInstance.on === 'function') {
                // Use once to automatically remove listener after first call
                plotInstance.once('plotly_afterplot', handleAfterPlot);
            } else if (spatialDiv && spatialDiv._fullLayout) {
                // Fallback: try to get plot instance from spatialDiv
                const divPlotInstance = spatialDiv._fullLayout;
                if (divPlotInstance && typeof divPlotInstance.on === 'function') {
                    divPlotInstance.once('plotly_afterplot', handleAfterPlot);
                } else {
                    // Last resort: use setTimeout as fallback
                    setTimeout(() => {
                        if (!resolved) {
                            resolved = true;
                            dispatch("plotRendered");
                            resolve();
                        }
                    }, 500);
                }
            } else {
                // No plot instance available, use timeout fallback
                setTimeout(() => {
                    if (!resolved) {
                        resolved = true;
                        dispatch("plotRendered");
                        resolve();
                    }
                }, 500);
            }
            
            // Fallback timeout in case event doesn't fire
            setTimeout(() => {
                if (!resolved) {
                    resolved = true;
                    dispatch("plotRendered");
                    resolve();
                }
            }, 2000);
        });
    }

    async function bindPlotEvents() {
        if (!plotInstance) return;

        plotInstance.on("plotly_selected", (eventData) => {
            if (eventData?.points && eventData.points.length > 0) {
                // Save current drag mode before redrawing
                if (plotInstance && plotInstance.layout) {
                    currentDragMode = plotInstance.layout.dragmode;
                }
                
                // Extract barcodes from the new selection
                const newBarcodes = eventData.points.map((pt) => pt.customdata);
                
                // Create a new region with a unique ID and color
                const newRegion = {
                    id: regionIdCounter++,
                    barcodes: newBarcodes,
                    color: regionColors[selectedRegions.length % regionColors.length],
                };
                
                // Add the new region (accumulate, don't replace)
                selectedRegions = [...selectedRegions, newRegion];
                
                // Clear the selection path visually but keep the mode active
                setTimeout(() => {
                    // Clear selection paths to allow next selection
                    const selectionPaths = document.querySelectorAll(".selectionlayer path");
                    selectionPaths.forEach(path => path.remove());
                    
                    // Redraw the plot to show all selected regions
                    drawPlot();
                }, 100);
                
                // Dispatch event with all selected regions
                dispatch("regionsSelected", {
                    regions: selectedRegions,
                    allBarcodes: getAllSelectedBarcodes(),
                });
            }
        });

        plotInstance.on("plotly_deselect", () => {
            // Don't auto-clear on deselect for multi-region mode
            // User can manually clear if needed
        });

        // Track drag mode changes
        plotInstance.on("plotly_relayout", (eventData) => {
            if (eventData && eventData.dragmode) {
                currentDragMode = eventData.dragmode;
            }
        });

        plotInstance.on("plotly_hover", (eventData) => {
            const point = eventData.points?.[0];
            if (point) {
                dispatch("hover", {
                    barcode: point.customdata,
                    from: "downstreamPlot",
                    persistent: false,
                });
            }
        });

        plotInstance.on("plotly_unhover", () => {
            dispatch("hover", {
                barcode: -1,
                from: "downstreamPlot",
                persistent: false,
            });
        });

        window.addEventListener("resize", () => {
            if (spatialDiv && spatialDiv.isConnected && plotInstance) {
                Plotly.Plots.resize(spatialDiv);
            }
        });
    }

    function clearAllRegions() {
        selectedRegions = [];
        drawPlot();
        dispatch("regionsSelected", {
            regions: [],
            allBarcodes: [],
        });
    }

    function removeRegion(regionId) {
        selectedRegions = selectedRegions.filter(r => r.id !== regionId);
        drawPlot();
        dispatch("regionsSelected", {
            regions: selectedRegions,
            allBarcodes: getAllSelectedBarcodes(),
        });
    }

    // Expose functions for parent component
    export function clearRegions() {
        clearAllRegions();
    }

    // Expose drawPlot function for parent component to trigger redraw
    export function redraw() {
        if (spatialData && image && spatialDiv && spatialData.length > 0 && clusterColorScale && typeof clusterColorScale === 'function') {
            drawPlot();
        }
    }

    // Reactive statements
    // Trigger redraw when spatialData, image, or clusterColorScale changes
    $: if (spatialData && image && spatialDiv && spatialData.length > 0 && plotInitialized && clusterColorScale && typeof clusterColorScale === 'function') {
        // Use clusterColorScaleVersion to detect color scale updates
        // Explicitly reference it to trigger reactivity
        const version = clusterColorScaleVersion;
        console.log("🔄 DownstreamPlot: Triggering redraw", {
            version,
            tracesCount: spatialData.length,
            hasColorScale: !!clusterColorScale
        });
        tick().then(() => {
            drawPlot();
        });
    }
    
    // Separate reactive statement specifically for clusterColorScaleVersion changes
    $: if (clusterColorScaleVersion !== undefined && plotInitialized && spatialData && spatialData.length > 0 && image && spatialDiv && clusterColorScale && typeof clusterColorScale === 'function') {
        console.log("🎨 DownstreamPlot: clusterColorScaleVersion changed, redrawing", {
            version: clusterColorScaleVersion,
            hasColorScale: !!clusterColorScale
        });
        tick().then(() => {
            drawPlot();
        });
    }

    $: if (hoveredBarcode?.from === "umap" && spatialDiv && plotInstance) {
        // Handle hover from UMAP if needed
    }

    let plotInitialized = false;

    onMount(() => {
        plotInitialized = true;
        resizeObserver = new ResizeObserver(() => {
            if (plotInstance && spatialDiv && spatialDiv.isConnected) {
                Plotly.Plots.resize(spatialDiv);
            }
        });
        if (spatialDiv) {
            resizeObserver.observe(spatialDiv);
        } else {
            tick().then(() => {
                if (spatialDiv && resizeObserver) {
                    resizeObserver.observe(spatialDiv);
                }
            });
        }
    });

    // Load attention flow data when slice is "hbrc"
    async function loadAttentionFlow() {
        if (currentSlice !== "hbrc") {
            attentionFlowData = [];
            return;
        }

        try {
            console.log("Loading attention flow data...");
            const startTime = Date.now();
            const response = await fetch(`${baseApi}/attention-flow?slice_id=${currentSlice}`);
            if (response.ok) {
                const data = await response.json();
                attentionFlowData = data || [];
                const loadTime = Date.now() - startTime;
                console.log(`Attention flow data loaded: ${attentionFlowData.length} vectors in ${loadTime}ms`);
                // Redraw plot to show attention flow vectors
                if (showAttentionFlow && plotInstance) {
                    drawPlot();
                }
            } else {
                console.warn("Failed to load attention flow data:", response.status);
                attentionFlowData = [];
            }
        } catch (error) {
            console.error("Error loading attention flow data:", error);
            attentionFlowData = [];
        }
    }

    // Reactive statement to load attention flow when slice changes
    $: if (currentSlice === "hbrc" && !attentionFlowData.length) {
        loadAttentionFlow();
    }

    // Reactive statement to redraw when showAttentionFlow changes
    // 使用防抖避免频繁重绘
    let redrawTimeout = null;
    // Remove plotInstance from dependencies to avoid infinite loop (drawPlot updates plotInstance)
    $: if (plotInitialized && spatialDiv && image) {
        // Explicitly depend on showAttentionFlow and attentionFlowData
        const _trigger = [showAttentionFlow, attentionFlowData];
        
        if (redrawTimeout) {
            clearTimeout(redrawTimeout);
        }
        redrawTimeout = setTimeout(() => {
            if (showAttentionFlow && attentionFlowData.length > 0) {
                drawPlot();
            } else if (!showAttentionFlow) {
                drawPlot();
            }
        }, 100); // 100ms 防抖
    }

    // Export function to toggle attention flow display
    export function toggleAttentionFlow() {
        showAttentionFlow = !showAttentionFlow;
        if (showAttentionFlow && !attentionFlowData.length && currentSlice === "hbrc") {
            loadAttentionFlow();
        }
    }

    // Export function to check if attention flow is available
    export function isAttentionFlowAvailable() {
        return currentSlice === "hbrc";
    }

    onDestroy(() => {
        if (resizeObserver) {
            if (spatialDiv) {
                resizeObserver.unobserve(spatialDiv);
            }
            resizeObserver.disconnect();
        }
    });
</script>

<div class="relative w-full h-full">
    <div class="h-full" bind:this={spatialDiv}></div>
    
    {#if selectedRegions.length > 0}
        <div class="absolute top-2 left-2 z-10 bg-white/90 backdrop-blur-sm rounded-lg border border-gray-300 shadow-lg p-3 max-w-xs">
            <div class="flex items-center justify-between mb-2">
                <h3 class="text-sm font-semibold text-gray-700">Selected Regions ({selectedRegions.length})</h3>
                <button
                    class="text-xs text-red-600 hover:text-red-800 px-2 py-1 rounded hover:bg-red-50"
                    on:click={clearAllRegions}
                >
                    Clear All
                </button>
            </div>
            <div class="space-y-1 max-h-32 overflow-y-auto">
                {#each selectedRegions as region (region.id)}
                    <div class="flex items-center justify-between text-xs py-1 px-2 rounded hover:bg-gray-50">
                        <div class="flex items-center gap-2">
                            <div 
                                class="w-3 h-3 rounded-full border border-gray-300"
                                style="background-color: {region.color};"
                            ></div>
                            <span class="text-gray-600">Region {region.id + 1}: {region.barcodes.length} spots</span>
                        </div>
                        <button
                            class="text-red-500 hover:text-red-700 ml-2"
                            on:click={() => removeRegion(region.id)}
                            title="Remove this region"
                        >
                            ×
                        </button>
                    </div>
                {/each}
            </div>
        </div>
    {/if}
    
    {#if selectedRegions.length === 0}
        <!-- <div class="absolute top-2 left-2 z-10 bg-blue-50/90 backdrop-blur-sm px-3 py-1.5 rounded-md border border-blue-200 shadow-sm text-xs text-blue-700 pointer-events-none">
            💡 Use lasso/select tool to select regions (multiple selections supported)
        </div> -->
    {/if}
</div>

<style>
    :global(.plotly .infolayer) {
        z-index: 9999 !important;
        pointer-events: none;
    }
</style>

