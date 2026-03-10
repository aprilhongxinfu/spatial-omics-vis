<script>
    import { createEventDispatcher, onDestroy, tick } from "svelte";
    import Plotly from "plotly.js-dist-min";
    import * as d3 from "d3";

    export let clusterColorScale;
    export let umapData = [];
    export let hoveredBarcode = { barcode: -1, from: "spotPlot", persistent: false };
    export let refreshToken = 0;
    export let clickedInfo = null;
    export let lassoSelected = false;
    export let lassoHover;

    const dispatch = createEventDispatcher();

    let umapDiv;
    let plotInstance = null;
    let resizeObserver;

    const plotConfig = {
        scrollZoom: true,
        responsive: true,
        useResizeHandler: true,
        displaylogo: false,
        modeBarButtons: [["toImage"]],
    };

    // Cache last hovered barcode to avoid redundant dispatches
    let lastHoveredBarcode = null;
    let rafId = null;
    
    function attachEventHandlers() {
        if (!plotInstance || !umapDiv) return;

        plotInstance.on("plotly_hover", (eventData) => {
            const point = eventData.points?.[0];
            if (!point) return;
            
            const barcode = point.text;
            
            // Skip if same barcode (avoid redundant updates)
            if (lastHoveredBarcode === barcode) return;
            lastHoveredBarcode = barcode;
            
            // Cancel any pending animation frame
            if (rafId !== null) {
                cancelAnimationFrame(rafId);
            }
            
            // Use requestAnimationFrame for smooth updates
            rafId = requestAnimationFrame(() => {
                dispatch("hover", {
                    barcode: barcode,
                    from: "umap",
                    persistent: false,
                });
                rafId = null;
            });
        });

        plotInstance.on("plotly_unhover", () => {
            // Cancel any pending animation frame
            if (rafId !== null) {
                cancelAnimationFrame(rafId);
                rafId = null;
            }
            lastHoveredBarcode = null;
            
            // In lasso mode, always clear hover to allow tooltip to disappear immediately
            // Otherwise, only clear if no clickedInfo
            if (lassoSelected) {
                // Always clear in lasso mode to ensure plot tooltip disappears
                dispatch("hover", {
                    barcode: -1,
                    from: "umap",
                    persistent: false,
                });
            } else if (!clickedInfo) {
                // Not in lasso mode, only clear if no clickedInfo
                dispatch("hover", {
                    barcode: -1,
                    from: "umap",
                    persistent: false,
                });
            }
        });
    }

    function observeResize() {
        if (!umapDiv || typeof ResizeObserver === "undefined") return;
        if (resizeObserver) {
            resizeObserver.disconnect();
        }
        resizeObserver = new ResizeObserver(() => {
            if (umapDiv && umapDiv.isConnected && plotInstance) {
                Plotly.Plots.resize(umapDiv);
            }
        });
        resizeObserver.observe(umapDiv);
    }

    // 从clickedInfo中提取选中的barcode列表
    function extractSelectedBarcodes() {
        if (!clickedInfo) return new Set();
        
        const selectedBarcodes = new Set();
        
        if (Array.isArray(clickedInfo)) {
            clickedInfo.forEach((item) => {
                if (typeof item === "string" || typeof item === "number") {
                    selectedBarcodes.add(`${item}`.trim());
                } else if (typeof item === "object" && item) {
                    const barcode = item.barcode ?? item.Barcode ?? item.id ?? item?.barcode_id;
                    if (barcode) {
                        selectedBarcodes.add(`${barcode}`.trim());
                    }
                }
            });
        } else if (typeof clickedInfo === "object" && clickedInfo.barcode) {
            selectedBarcodes.add(`${clickedInfo.barcode}`.trim());
        }
        
        return selectedBarcodes;
    }

    async function drawUMAP() {
        if (!umapDiv || !umapDiv.isConnected) return;
        if (!Array.isArray(umapData) || !umapData.length) {
            if (plotInstance) {
                Plotly.purge(umapDiv);
                plotInstance = null;
            }
            return;
        }
        if (!clusterColorScale || typeof clusterColorScale !== "function") {
            return;
        }

        const layout = {
            margin: { l: 40, r: 10, t: 10, b: 30 },
            showlegend: false,
            autosize: true,
            title: "",
        };

        const grouped = Array.from(d3.group(umapData, (d) => d.cluster)).sort(
            (a, b) => `${a[0]}`.localeCompare(`${b[0]}`, undefined, { numeric: true }),
        );

        // 提取套索选中的barcode集合
        const selectedBarcodesSet = lassoSelected ? extractSelectedBarcodes() : new Set();
        const hasLassoSelection = lassoSelected && selectedBarcodesSet.size > 0;

        const traces = grouped.map(([cluster, points]) => {
            const barcodes = points.map((d) => d.barcode);
            
            // 在 lasso 模式下，不考虑 hover 高亮，只显示选中的点
            // 在非 lasso 模式下，才考虑 hover 高亮
            const hoveredIndex = !lassoSelected ? barcodes.indexOf(hoveredBarcode?.barcode ?? "") : -1;
            const isHovering = !lassoSelected &&
                hoveredBarcode?.barcode &&
                hoveredBarcode?.barcode !== "" &&
                hoveredBarcode?.barcode !== -1;

            // 计算应该高亮的点索引
            let selectedIndices = null;
            if (hasLassoSelection) {
                // 套索模式：高亮所有选中的点（不考虑 hover）
                selectedIndices = barcodes
                    .map((barcode, idx) => selectedBarcodesSet.has(`${barcode}`.trim()) ? idx : -1)
                    .filter(idx => idx !== -1);
                if (selectedIndices.length === 0) {
                    selectedIndices = null;
                }
            } else if (isHovering && hoveredIndex !== -1) {
                // 普通hover模式：只高亮hover的点
                selectedIndices = [hoveredIndex];
            }

            // 根据是否有套索选择来设置点的样式
            if (hasLassoSelection && selectedIndices && selectedIndices.length > 0) {
                // 套索模式：为选中的点和未选中的点分别设置样式
                const selectedSet = new Set(selectedIndices);
                return {
                    x: points.map((d) => d.UMAP_1),
                    y: points.map((d) => d.UMAP_2),
                    text: barcodes,
                    name: `Cluster ${cluster}`,
                    type: "scatter",
                    mode: "markers",
                    marker: {
                        color: barcodes.map((_, idx) => clusterColorScale(cluster)),
                        size: barcodes.map((_, idx) => selectedSet.has(idx) ? 6 : 4),
                        opacity: barcodes.map((_, idx) => selectedSet.has(idx) ? 1 : 0.1),
                    },
                    hovertemplate: "Barcode: %{text}<extra></extra>",
                };
            } else {
                // 普通模式或没有选中点的情况
                const defaultOpacity = hasLassoSelection && (!selectedIndices || selectedIndices.length === 0) ? 0.1 : 1;
            return {
                x: points.map((d) => d.UMAP_1),
                y: points.map((d) => d.UMAP_2),
                text: barcodes,
                name: `Cluster ${cluster}`,
                type: "scatter",
                mode: "markers",
                marker: {
                    color: clusterColorScale(cluster),
                    size: 4,
                        opacity: hasLassoSelection ? defaultOpacity : (isHovering ? 0.1 : 1),
                },
                    selectedpoints: selectedIndices,
                    selected: { marker: { opacity: 1, size: 6 } },
                unselected: { marker: { opacity: 0.1 } },
                hovertemplate: "Barcode: %{text}<extra></extra>",
            };
            }
        });

        if (plotInstance) {
            await Plotly.react(umapDiv, traces, layout, plotConfig);
        } else {
            plotInstance = await Plotly.newPlot(umapDiv, traces, layout, plotConfig);
            attachEventHandlers();
            observeResize();
            window.addEventListener("resize", handleWindowResize);
        }
    }

    function handleWindowResize() {
        if (umapDiv && umapDiv.isConnected && plotInstance) {
            Plotly.Plots.resize(umapDiv);
        }
    }

    $: drawNonce =
        umapDiv &&
        umapDiv.isConnected &&
        Array.isArray(umapData) &&
        umapData.length &&
        clusterColorScale &&
        typeof clusterColorScale === "function"
            ? `${refreshToken}:${umapData.length}`
            : null;

    $: if (drawNonce) {
        tick().then(drawUMAP);
    }

    // In lasso mode, don't redraw on hover - only update annotations
    // Outside lasso mode, redraw to show hover highlight
    $: if (hoveredBarcode?.from === "spotPlot" && drawNonce && !lassoSelected) {
        drawUMAP();
    }

    // 当hoveredBarcode变为-1且没有选中点和lasso时，恢复UMAP显示
    $: if (
        hoveredBarcode?.barcode === -1 &&
        !clickedInfo &&
        !lassoSelected &&
        plotInstance &&
        drawNonce
    ) {
        drawUMAP();
    }

    // 当套索选择状态或clickedInfo变化时，重新绘制UMAP以更新高亮
    // 合并这两个响应式语句，避免重复绘制
    $: if (lassoSelected && drawNonce) {
        drawUMAP();
    }

    // Cache for current annotation to avoid unnecessary relayout calls
    let currentAnnotationBarcode = null;
    let annotationRafId = null;
    
    // Handle tooltips in lasso mode: lassoHover takes precedence over plot hover
    $: if (plotInstance && umapDiv) {
        // Check if lassoHover is active (has barcode) - this takes highest priority
        const isLassoHoverActive = lassoHover && lassoHover.barcode;
        
        // Cancel any pending animation frame
        if (annotationRafId !== null) {
            cancelAnimationFrame(annotationRafId);
            annotationRafId = null;
        }
        
        if (isLassoHoverActive) {
            // Show lassoHover tooltip (from table row hover)
            const barcode = lassoHover.barcode;
            
            // Only update if barcode changed
            if (currentAnnotationBarcode !== barcode) {
                currentAnnotationBarcode = barcode;
                
                // Use requestAnimationFrame to batch updates
                annotationRafId = requestAnimationFrame(() => {
                    // Find the point in umapData
                    const point = umapData.find((d) => `${d.barcode}`.trim() === `${barcode}`.trim());
                    if (point) {
                        Plotly.relayout(umapDiv, {
                            annotations: [
                                {
                                    x: point.UMAP_1,
                                    y: point.UMAP_2,
                                    text: `Barcode: ${barcode}`,
                                    showarrow: true,
                                    arrowhead: 1,
                                    ax: 0,
                                    ay: -40,
                                    bgcolor: "rgba(0, 0, 0, 0.8)",
                                    bordercolor: "",
                                    borderwidth: 1,
                                    font: { color: "white", size: 12 },
                                    layer: "above",
                                },
                            ],
                        });
                    } else {
                        // Point not found, clear annotations
                        Plotly.relayout(umapDiv, {
                            annotations: [],
                        });
                        currentAnnotationBarcode = null;
                    }
                    annotationRafId = null;
                });
            }
        } else if (lassoSelected) {
            // Show tooltip when hovering from plot (only if lassoHover is not active)
            const isFromPlot = hoveredBarcode?.from === "spotPlot";
            const hovered = hoveredBarcode?.barcode;
            
            if (isFromPlot && hovered && hovered !== -1 && hovered !== "") {
                // Only update if barcode changed
                if (currentAnnotationBarcode !== hovered) {
                    currentAnnotationBarcode = hovered;
                    
                    // Use requestAnimationFrame to batch updates
                    annotationRafId = requestAnimationFrame(() => {
                        // Find the point in umapData
                        const point = umapData.find((d) => `${d.barcode}`.trim() === `${hovered}`.trim());
                        if (point) {
                            Plotly.relayout(umapDiv, {
                                annotations: [
                                    {
                                        x: point.UMAP_1,
                                        y: point.UMAP_2,
                                        text: `Barcode: ${hovered}`,
                                        showarrow: true,
                                        arrowhead: 1,
                                        ax: 0,
                                        ay: -40,
                                        bgcolor: "rgba(0, 0, 0, 0.8)",
                                        bordercolor: "",
                                        borderwidth: 1,
                                        font: { color: "white", size: 12 },
                                        layer: "above",
                                    },
                                ],
                            });
                        } else {
                            // Point not found, clear annotations
                            Plotly.relayout(umapDiv, {
                                annotations: [],
                            });
                            currentAnnotationBarcode = null;
                        }
                        annotationRafId = null;
                    });
                }
            } else {
                // Not hovering from plot, or hover ended, or invalid barcode - clear tooltip immediately
                if (currentAnnotationBarcode !== null) {
                    // Clear immediately without requestAnimationFrame for better responsiveness
                    Plotly.relayout(umapDiv, {
                        annotations: [],
                    });
                    currentAnnotationBarcode = null;
                }
            }
        } else {
            // Not in lasso mode, clear annotation if exists
            if (currentAnnotationBarcode !== null) {
                Plotly.relayout(umapDiv, {
                    annotations: [],
                });
                currentAnnotationBarcode = null;
            }
        }
    }

    onDestroy(() => {
        // Cancel any pending animation frames
        if (rafId !== null) {
            cancelAnimationFrame(rafId);
            rafId = null;
        }
        if (annotationRafId !== null) {
            cancelAnimationFrame(annotationRafId);
            annotationRafId = null;
        }
        
        if (plotInstance && umapDiv) {
            Plotly.purge(umapDiv);
            plotInstance = null;
        }
        if (resizeObserver && umapDiv) {
            resizeObserver.unobserve(umapDiv);
            resizeObserver.disconnect();
        }
        window.removeEventListener("resize", handleWindowResize);
    });
</script>

<div class="flex flex-col gap-2 h-full">
    <div class="text-sm font-semibold text-slate-700">UMAP</div>
    <div class="flex-1 min-h-0 w-full" bind:this={umapDiv}></div>
</div>

