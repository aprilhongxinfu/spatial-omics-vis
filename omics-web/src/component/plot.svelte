<script>
    import { createEventDispatcher, onMount, onDestroy, tick } from "svelte";
    import Plotly from "plotly.js-dist-min";
    import * as d3 from "d3";
    import SpotInspection from "./spotInspection.svelte";

    let spatialDiv;
    export let clickedInfo = null;
    export let spatialData;
    export let originalSpatialData = null; // Original spatialData before preview updates
    export let image;
    // export let imageUrl;
    export let clusterColorScale;
    export let hoveredBarcode;
    export let lassoHover;
    export let lassoSelected = false;
    export let baseApi;
    export let currentSlice;
    export let currentClusterResultId = "default";
export let lassoClearSignal = 0;

    let clusterEdit = false;
    let availableClusters = [];
    let selectedCluster = null;
    let prevSpatialData;
    let comment = "";
    // let image;
    const dispatch = createEventDispatcher();
    let resizeObserver;
    let selectedBarcodes = [];
    let prevSelectedBarcodes = [];
    let plotInstance = null;
    let previewUrl = "";
    let annotationVisible = false;
    let annotationText = "";
    let annotationPos = {};
    let annotationColor = "";
    let previewBox, previewImg, previewOverlay, previewCircle;
    let lastSX = 0,
        lastSY = 0,
        lastSW = 0,
        lastSH = 0;
    let plotInitialized = false;
    let imageReady = false;
    let blob;
    let isDoubleClick = false;
    let clickTimer = null;
    let tooltipPosition = null;
    let tooltipEl;
    let activeTraceIndex = null;
    let activePointIndex = null;
    let activeBarcode = null;
    // Cache for current annotation to avoid unnecessary relayout calls
    let currentUmapAnnotationBarcode = null;
    // Save plot layout state (zoom, pan, etc.) to preserve it across redraws
    let savedLayoutState = null;
let skipNextDeselect = false;
let lastLassoClearSignal = lassoClearSignal;
let prevClickedInfo = clickedInfo;

    // 当外部传入 clickedInfo 变化时，更新 selectedBarcodes
    $: if (clickedInfo !== prevClickedInfo) {
        prevClickedInfo = clickedInfo;
        // 直接从 clickedInfo 中提取 selectedBarcodes（不论 lassoSelected 状态）
        if (clickedInfo) {
            let newSelectedBarcodes = [];
            if (Array.isArray(clickedInfo)) {
                newSelectedBarcodes = clickedInfo
                    .map(item => {
                        if (typeof item === 'string' || typeof item === 'number') {
                            return `${item}`.trim();
                        } else if (typeof item === 'object' && item) {
                            return `${item.barcode ?? item.Barcode ?? item.id ?? item?.barcode_id ?? ''}`.trim();
                        }
                        return null;
                    })
                    .filter(bc => bc);
            } else if (typeof clickedInfo === 'object' && clickedInfo.barcode) {
                newSelectedBarcodes = [`${clickedInfo.barcode}`.trim()];
            }
            if (newSelectedBarcodes.length > 0) {
                selectedBarcodes = newSelectedBarcodes;
                console.log('[plot.svelte] Updated selectedBarcodes from clickedInfo:', selectedBarcodes.length, 'barcodes');
                // 如果 plotInstance 已初始化，重新应用 lasso 样式
                if (plotInstance) {
                    tick().then(() => {
                        reapplyLassoStyles();
                    });
                }
            }
        }
    }

    function clearPreview(
        notify = true,
        force = false,
        emitHover = true,
        resetMarkers = true,
    ) {
        if (!force && !previewUrl && !blob) return;

        console.log("clearPreview", resetMarkers);
        previewUrl = "";
        blob = null;
        lastSX = 0;
        lastSY = 0;
        lastSW = 0;
        lastSH = 0;
        tooltipPosition = null;
        if (resetMarkers) {
            resetMarkerStyles();
        }
        if (emitHover) {
            dispatch("hover", {
                barcode: -1,
                from: "spotPlot",
                persistent: false,
            });
        }

        if (notify) {
            dispatch("spotClick", {
                info: null,
                lassoSelected,
                previewUrl: null,
            });
        }
    }

    async function showTooltipAt(clientX = 0, clientY = 0) {
        if (typeof window === "undefined") {
            tooltipPosition = { left: clientX, top: clientY };
            return;
        }

        const margin = 16; 
        const viewportWidth = window.innerWidth;
        const viewportHeight = window.innerHeight;

        // Set initial position so the element exists before measuring
        tooltipPosition = {
            left: clientX,
            top: clientY,
        };

        await tick();

        if (!tooltipEl) return;

        const tooltipWidth = tooltipEl.offsetWidth ?? 0;
        const tooltipHeight = tooltipEl.offsetHeight ?? 0;

        let left = clientX - tooltipWidth / 2;
        let top = clientY - tooltipHeight - margin;

        if (top < margin) {
            top = Math.min(
                clientY + margin,
                viewportHeight - tooltipHeight - margin,
            );
        }

        if (left < margin) {
            left = margin;
        } else if (left + tooltipWidth + margin > viewportWidth) {
            left = Math.max(margin, viewportWidth - tooltipWidth - margin);
        }

        if (top + tooltipHeight + margin > viewportHeight) {
            top = viewportHeight - tooltipHeight - margin;
        }

        tooltipPosition = { left, top };
    }

    function resetMarkerStyles() {
        console.log("resetMarkerStyles");
        if (!plotInstance) return;
        activeTraceIndex = null;
        activePointIndex = null;
        activeBarcode = null;
        plotInstance.data.forEach((_, idx) => {
            Plotly.restyle(
                plotInstance,
                {
                    "marker.opacity": [1],
                    selectedpoints: [null],
                    "selected.marker.opacity": [1],
                    "unselected.marker.opacity": [1],
                },
                [idx],
            );
        });
    }

    function performLassoDeselect({ emitEvent = true } = {}) {
        console.log("performLassoDeselect");
        skipNextDeselect = false;
        clickedInfo = null;
        lassoSelected = false;
        clusterEdit = false;
        clearPreview(false, true);
        selectedBarcodes = [];
        prevSelectedBarcodes = [];
        activeTraceIndex = null;
        activePointIndex = null;
        activeBarcode = null;
        if (plotInstance) {
            resetMarkerStyles();
            Plotly.relayout(plotInstance, {
                annotations: [],
            });
        }
        if (emitEvent) {
            dispatch("spotClick", {
                info: clickedInfo,
                lassoSelected,
            });
        }
    }

    function applyActiveHighlight() {
        if (!plotInstance) return;
        if (
            activeTraceIndex === null ||
            activePointIndex === null ||
            !Array.isArray(plotInstance.data) ||
            !plotInstance.data[activeTraceIndex]
        ) {
            return;
        }

        const trace = plotInstance.data[activeTraceIndex];
        const pointCount =
            trace?.x?.length ??
            trace?.y?.length ??
            (trace?.customdata || trace?.text || []).length;

        if (
            typeof pointCount !== "number" ||
            pointCount <= activePointIndex ||
            activePointIndex < 0
        ) {
            return;
        }

        plotInstance.data.forEach((_, idx) => {
            if (idx === activeTraceIndex) {
                Plotly.restyle(
                    plotInstance,
                    {
                        "marker.opacity": [1],
                        selectedpoints: [[activePointIndex]],
                        "selected.marker.opacity": [1],
                        "unselected.marker.opacity": [0.15],
                    },
                    [idx],
                );
            } else {
                Plotly.restyle(
                    plotInstance,
                    {
                        "marker.opacity": [0.15],
                        selectedpoints: [null],
                        "selected.marker.opacity": [1],
                        "unselected.marker.opacity": [1],
                    },
                    [idx],
                );
            }
        });
    }

    function reapplyLassoStyles() {
        if (!plotInstance || !selectedBarcodes?.length) return;

        plotInstance.data.forEach((trace, idx) => {
            const barcodes = trace.customdata || trace.text || [];
            const indices = barcodes
                .map((bc, i) => (selectedBarcodes.includes(bc) ? i : -1))
                .filter((i) => i !== -1);

            const restylePayload =
                indices.length > 0
                    ? {
                          selectedpoints: [indices],
                          "marker.opacity": [1],
                          "selected.marker.opacity": [1],
                          "unselected.marker.opacity": [0.2],
                      }
                    : {
                          // 没有选中点的 trace 整体暗掉
                          selectedpoints: [null],
                          "marker.opacity": [0.2],
                          "selected.marker.opacity": [1],
                          "unselected.marker.opacity": [0.2],
                      };

            Plotly.restyle(plotInstance, restylePayload, [idx]);
        });
    }

    function updatePreviewCircle(p) {
        if (
            !previewImg ||
            !previewCircle ||
            !image ||
            !plotInstance ||
            !previewUrl
        )
            return;

        tick().then(() => {
            const displayWidth = previewImg.clientWidth;
            const displayHeight = previewImg.clientHeight;

            const imageObj = plotInstance.layout.images?.[0];
            if (!imageObj) return;

            // 原图大小
            const imgW = image.width;
            const imgH = image.height;

            const sizex = imageObj.sizex ?? 1;
            const sizey = imageObj.sizey ?? 1;
            const x0Image = imageObj.x;
            const y0Image = imageObj.y;

            // 当前点在原图的像素坐标
            const relX = (p.x - x0Image) * (imgW / sizex);
            const relY = (p.y - y0Image) * (imgH / sizey);
            const flippedY = imgH - relY;

            // === 关键部分 ===
            // 你需要记住上次框选区域的 sx, sy, sw, sh，才能做映射
            const canvas = new Image();
            canvas.src = previewUrl;

            canvas.onload = () => {
                // 拿到裁剪的尺寸
                const sx = lastSX;
                const sy = lastSY;
                const sw = lastSW;
                const sh = lastSH;

                // 将原图上的点位置转换到裁剪图上的相对位置
                const clippedX = (relX - sx) / sw;
                const clippedY = (relY - sy) / sh;

                // clamp 限制在 0-1 区间
                const clampedX = Math.max(0, Math.min(1, clippedX));
                const clampedY = Math.max(0, Math.min(1, clippedY));

                const cx = clampedX * displayWidth;
                const cy = clampedY * displayHeight;

                previewOverlay.setAttribute("width", displayWidth);
                previewOverlay.setAttribute("height", displayHeight);
                previewCircle.setAttribute("cx", cx);
                previewCircle.setAttribute("cy", cy);
            };
        });
    }

    // 添加一个响应式语句来处理spatialDiv的重新绑定
    $: if (spatialDiv && spatialData && imageReady && !plotInitialized) {
        // 使用tick确保DOM更新完成
        tick().then(() => {
            if (spatialDiv && spatialDiv.isConnected) {
        drawPlot();
        plotInitialized = true;
            }
        });
    }

    function tryDrawPlot() {
        if (spatialData && imageReady && spatialDiv) {
            drawPlot();
            plotInitialized = true;
        }
    }

    // 图像加载后才可绘制图层背景
    async function loadImage(url) {
        return new Promise((resolve) => {
            const img = new Image();
            img.crossOrigin = "anonymous";
            img.src = url;
            img.onload = () => resolve(img);
        });
    }

    // let prevSpatialData;
    let prevImage;
    // let plotInitialized = false;

    $: if (spatialData !== prevSpatialData) {
        // Save current layout state before redrawing if plot exists
        if (plotInstance && plotInstance.layout && lassoSelected) {
            savedLayoutState = {
                'xaxis.range': plotInstance.layout.xaxis?.range,
                'yaxis.range': plotInstance.layout.yaxis?.range,
                dragmode: plotInstance.layout.dragmode,
            };
        }
        
        // Clear active highlight when spatialData changes
        activeTraceIndex = null;
        activePointIndex = null;
        activeBarcode = null;
        
        prevSpatialData = spatialData;
        plotInitialized = false;
        // 如果lassoSelected为true，保持选中状态，否则重置
        if (!lassoSelected) {
            clickedInfo = null;
            selectedBarcodes = [];
            previewUrl = "";
            savedLayoutState = null;
        } else {
            // 如果lassoSelected为true，从clickedInfo中恢复selectedBarcodes
            if (Array.isArray(clickedInfo)) {
                selectedBarcodes = clickedInfo
                    .map(item => {
                        if (typeof item === 'string' || typeof item === 'number') {
                            return `${item}`.trim();
                        } else if (typeof item === 'object' && item) {
                            return `${item.barcode ?? item.Barcode ?? item.id ?? item?.barcode_id ?? ''}`.trim();
                        }
                        return null;
                    })
                    .filter(bc => bc);
            } else if (clickedInfo && typeof clickedInfo === 'object' && clickedInfo.barcode) {
                selectedBarcodes = [`${clickedInfo.barcode}`.trim()];
            }
        }
        tryDrawPlot();
    }

    $: if (image !== prevImage) {
        prevImage = image;
        plotInitialized = false;
        // 如果 lassoSelected 为 true（外部传入），保持选中状态
        if (!lassoSelected) {
            clickedInfo = null;
            selectedBarcodes = [];
            previewUrl = "";
        }
        tryDrawPlot();
    }

    $: if (hoveredBarcode?.from === "umap" && spatialDiv) {
        drawPlot();
    }

    // 当hoveredBarcode变为-1且没有选中点和lasso时，恢复plot显示
    $: if (
        hoveredBarcode?.barcode === -1 &&
        !clickedInfo &&
        !lassoSelected &&
        plotInstance &&
        spatialDiv
    ) {
        resetMarkerStyles();
        // 恢复所有点的正常显示
        plotInstance.data.forEach((_, idx) => {
            Plotly.restyle(
                plotInstance,
                {
                    "marker.opacity": [1],
                    selectedpoints: [null],
                    "selected.marker.opacity": [1],
                    "unselected.marker.opacity": [1],
                },
                [idx],
            );
        });
    }

    function toBase64(img) {
        const canvas = document.createElement("canvas");
        canvas.width = img.width;
        canvas.height = img.height;
        const ctx = canvas.getContext("2d");
        ctx.drawImage(img, 0, 0);
        return canvas.toDataURL("image/png");
    }

    async function drawPlot() {
        if (plotInstance && spatialDiv) {
            Plotly.purge(spatialDiv);
            plotInstance = null;
        }
        // image = await loadImage(imageUrl);
        if (!image) {
            console.warn("❌ image not loaded yet");
            return;
        }
        
        // 检查spatialDiv是否存在
        if (!spatialDiv || !spatialDiv.isConnected) {
            console.warn("❌ spatialDiv not available yet");
            return;
        }
        
        // 检查clusterColorScale是否已初始化
        if (!clusterColorScale || typeof clusterColorScale !== 'function') {
            console.warn("❌ clusterColorScale not initialized yet");
            return;
        }
        
        const base64 = toBase64(image);
        
        // Use saved layout state if available (to preserve zoom/pan), otherwise use defaults
        const defaultXRange = [0, image.width];
        const defaultYRange = [image.height, 0];
        
        const layout = {
            autosize: true,
            title: "Spatial Clusters",
            uirevision: lassoSelected ? 'preserve' : undefined, // Preserve UI state when lasso is selected
            xaxis: {
                visible: false,
                range: savedLayoutState?.['xaxis.range'] || defaultXRange,
            },
            yaxis: {
                visible: false,
                range: savedLayoutState?.['yaxis.range'] || defaultYRange,
                scaleanchor: "x",
                scaleratio: 1,
            },
            dragmode: savedLayoutState?.dragmode || false,
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

        // Normalize cluster name to match color scale domain
        function normalizeClusterName(name) {
            return `${name ?? ""}`
                .trim()
                .replace(/^cluster\s+/i, "")
                .replace(/^cluster_/i, "")
                .trim();
        }

        const traces = spatialData.map((trace) => {
            const barcodes = trace.customdata || trace.text || [];
            const normalizedHovered = hoveredBarcode?.barcode 
                ? `${hoveredBarcode.barcode}`.trim() 
                : "";
            
            // Find hovered index by comparing normalized strings
            let hoveredIndex = -1;
            if (normalizedHovered) {
                for (let i = 0; i < barcodes.length; i++) {
                    const normalizedBarcode = `${barcodes[i]}`.trim();
                    if (normalizedBarcode === normalizedHovered) {
                        hoveredIndex = i;
                        break;
                    }
                }
            }

            const isHovering =
                hoveredBarcode?.barcode &&
                hoveredBarcode?.barcode !== "" &&
                hoveredBarcode?.barcode !== -1;

            let selectedIndices = null;
            if (lassoSelected && selectedBarcodes.length > 0) {
                selectedIndices = barcodes
                    .map((bc, i) => (selectedBarcodes.includes(bc) ? i : -1))
                    .filter((i) => i !== -1);
            }

            // Normalize trace name to ensure we get the aligned color from the color scale
            // Try multiple formats to match the color scale domain (which includes variants)
            const normalizedName = normalizeClusterName(trace.name);
            const traceNameStr = `${trace.name}`.trim();
            const normalizedNameStr = `${normalizedName}`.trim();
            
            // Try multiple key formats to match the color scale domain
            let color = clusterColorScale(normalizedNameStr) || 
                       clusterColorScale(traceNameStr) ||
                       clusterColorScale(normalizedName) ||
                       clusterColorScale(trace.name);
            
            // If still not found, try numeric variants (e.g., "0" vs 0)
            if (!color || color === "#999999") {
                const numericValue = Number.parseFloat(normalizedNameStr);
                if (!Number.isNaN(numericValue)) {
                    color = clusterColorScale(`${numericValue}`) ||
                           clusterColorScale(`${numericValue.toFixed(0)}`) ||
                           clusterColorScale(numericValue);
                }
            }
            
            // Final fallback
            if (!color || color === "#999999") {
                color = clusterColorScale(traceNameStr);
            }

            // Only set selectedpoints if we have lasso selection or hover, not for preview updates
            // Clear selectedpoints when just updating preview (no hover, no lasso selection)
            const shouldHighlight = (isHovering && hoveredIndex !== -1) || (lassoSelected && selectedIndices && selectedIndices.length > 0);
            
            return {
                ...trace,
                marker: {
                    ...trace.marker,
                    color: color,
                    opacity: isHovering ? 0.2 : 1, // 非高亮点透明
                },
                name: `Cluster ${trace.name}`,
                selectedpoints: shouldHighlight
                    ? (isHovering && hoveredIndex !== -1 ? [hoveredIndex] : selectedIndices)
                    : null,
                selected: { marker: { opacity: 1 } },
                unselected: { marker: { opacity: 0.2 } },
            };
        });

        const clusterSet = new Set();
        spatialData.forEach((trace) => clusterSet.add(trace.name));
        availableClusters = Array.from(clusterSet);

        // ⚠️ 渲染并存下实例
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
        // Clear active highlight when redrawing (to prevent unwanted highlighting)
        activeTraceIndex = null;
        activePointIndex = null;
        activeBarcode = null;
        applyActiveHighlight();
        // Reapply lasso styles if lassoSelected is true
        if (lassoSelected && selectedBarcodes.length > 0) {
            reapplyLassoStyles();
            // Restore selection visual if dragmode is lasso or select
            if (savedLayoutState?.dragmode === 'lasso' || savedLayoutState?.dragmode === 'select') {
                // The selection visual will be maintained by Plotly's uirevision
                // We just need to ensure the dragmode is set correctly
                tick().then(() => {
                    if (plotInstance && plotInstance.layout) {
                        Plotly.relayout(plotInstance, {
                            dragmode: savedLayoutState.dragmode,
                        });
                    }
                });
            }
        }
    }

    async function bindPlotEvents() {
        if (!plotInstance) return;

        plotInstance.on("plotly_selected", (eventData) => {
            (async () => {
                // const lassoPaths = document.querySelectorAll(
                //     ".selectionlayer path",
                // );
                // const lassoCircles = document.querySelectorAll(
                //     ".outline-controllers circle",
                // );
                // lassoPaths.forEach((path) => path.remove());
                // lassoCircles.forEach((circle) => circle.remove());
                clickedInfo = null;
                clusterEdit = false;
                lassoSelected = true;
                // resetMarkerStyles();
                tooltipPosition = null;
                
                // 隐藏所有 annotation
                Plotly.relayout(plotInstance, {
                    annotations: [],
                });

                if (eventData?.points) {
                    if (eventData.range) {
                        const {
                            x: [x0, x1],
                            y: [y0, y1],
                        } = eventData.range;

                        const imageObj = plotInstance.layout.images?.[0];
                        if (!imageObj) {
                            console.warn("No layout.images found!");
                            return;
                        }

                        // Step 1: 图像逻辑空间中左上角
                        const x0Image = imageObj.x;
                        const y0Image = imageObj.y;

                        // Step 2: 选择框逻辑边界（Plotly坐标系）
                        const x0Sel = Math.min(x0, x1);
                        const x1Sel = Math.max(x0, x1);
                        const y0Sel = Math.min(y0, y1);
                        const y1Sel = Math.max(y0, y1);

                        // Step 3: 逻辑坐标到像素坐标缩放比
                        const scaleX = image.width / (imageObj.sizex ?? 1);
                        const scaleY = image.height / (imageObj.sizey ?? 1);

                        // Step 4: 转换到图像像素坐标系（Canvas）
                        const sx = (x0Sel - x0Image) * scaleX;
                        const sw = (x1Sel - x0Image) * scaleX - sx;

                        const sy = (y0Sel - y0Image) * scaleY;
                        const sh = (y1Sel - y0Image) * scaleY - sy;

                        lastSX = sx;
                        lastSY = sy;
                        lastSW = sw;
                        lastSH = sh;

                        const sizex = imageObj.sizex ?? 1;
                        console.log("eventData.range:", eventData.range);
                        console.log("canvas draw params:", { sx, sy, sw, sh });
                        console.log(
                            "image dimensions",
                            image.width,
                            image.height,
                        );

                        // 创建 canvas 并画图
                        const canvas = document.createElement("canvas");
                        canvas.width = sw;
                        canvas.height = sh;
                        const ctx = canvas.getContext("2d");
                        ctx.drawImage(image, sx, sy, sw, sh, 0, 0, sw, sh);

                        previewUrl = canvas.toDataURL();
                        blob = base64ToBlob(previewUrl);
                    }

                    const barcodes = eventData.points.map(
                        (pt) => pt.customdata,
                    );
                    selectedBarcodes = barcodes;
                    prevSelectedBarcodes = barcodes;
                    console.log("Selected barcodes:", barcodes);

                    reapplyLassoStyles();

                    setTimeout(() => {
                        document
                            .querySelectorAll(".selectionlayer path")
                            .forEach((el) => {
                                el.setAttribute(
                                    "style",
                                    el
                                        .getAttribute("style")
                                        ?.replace(
                                            /pointer-events:\s*[^;]+;?/g,
                                            "",
                                        )
                                        ?.replace(/cursor:\s*[^;]+;?/g, "") ??
                                        "",
                                );

                                el.style.pointerEvents = "none";
                                el.style.cursor = "default";
                            });
                    }, 0);

                    dispatch("spotClick", {
                        info: barcodes,
                        lassoSelected: true,
                        previewUrl: blob,
                    });
                    dispatch("hover", {
                        barcode: -1,
                        from: "spotPlot",
                        persistent: false,
                    });
                }
            })();
        });

        plotInstance.on("plotly_deselect", () => {
            if (skipNextDeselect) {
                skipNextDeselect = false;
                reapplyLassoStyles();
                return;
            }
            performLassoDeselect();
        });

        plotInstance.on("plotly_click", async (eventData) => {
            // Check immediately if it's part of a double-click
            if (isDoubleClick) {
                return;
            }
            
            // Clear any existing click timer
            if (clickTimer) {
                clearTimeout(clickTimer);
            }
            
            const points = Array.isArray(eventData.points)
                ? eventData.points
                : [];
            const point = points.find(
                (pt) =>
                    pt &&
                    typeof pt.pointNumber === "number" &&
                    typeof pt.curveNumber === "number",
            );
            if (!point) {
                if (lassoSelected && selectedBarcodes.length) {
                    skipNextDeselect = true;
                    setTimeout(() => {
                        reapplyLassoStyles();
                        skipNextDeselect = false;
                    }, 0);
                }
                return;
            }
            const barcode = point.customdata;
            const traceIndex = point.curveNumber;
            const pointIndex = point.pointNumber;
            
            clusterEdit = false;
            lassoSelected = false;
            selectedBarcodes = [barcode];
            activeTraceIndex = traceIndex;
            activePointIndex = pointIndex;
            activeBarcode = barcode;
            clearPreview(false, false, true, false);
            
            // Use a timer to delay click processing, so we can detect double-click
            clickTimer = setTimeout(async () => {
                // Double-check if it's a double-click before processing
                if (isDoubleClick) {
                    clickTimer = null;
                    return;
                }
                
                clickedInfo = { barcode, loading: true };

                selectedCluster = point.data.name;
                // In manual mode, use original cluster from originalSpatialData if available
                // to prevent preview updates from affecting tooltip display
                const originalCluster = findOriginalClusterByBarcode(barcode);
                clickedInfo = {
                    barcode,
                    x: point.x,
                    y: point.y,
                    cluster: originalCluster ?? point.data.name.replace(/^cluster\s*/i, ""),
                };

                Plotly.relayout(spatialDiv, {
                    annotations: [],
                });

                const clientX = eventData.event?.clientX ?? 0;
                const clientY = eventData.event?.clientY ?? 0;
                await showTooltipAt(clientX, clientY);

                dispatch("spotClick", {
                    info: clickedInfo,
                    lassoSelected,
                    previewUrl: null,
                    pointer: {
                        clientX,
                        clientY,
                    },
                });

                dispatch("hover", {
                    barcode,
                    from: "spotPlot",
                    persistent: true,
                });
                
                clickTimer = null;
            }, 250); // 250ms delay to detect double-click

            // Fade other points while keeping the clicked point highlighted
            applyActiveHighlight();
        });

        function approxEqual(a, b, tol = 1e-2) {
            return Math.abs(a - b) < tol;
        }

        plotInstance.on("plotly_relayout", (eventData) => {
            // Save layout state when user zooms/pans or changes dragmode
            if (plotInstance && plotInstance.layout && lassoSelected) {
                if (eventData['xaxis.range'] || eventData['yaxis.range'] || eventData.dragmode) {
                    savedLayoutState = {
                        'xaxis.range': eventData['xaxis.range'] || plotInstance.layout.xaxis?.range,
                        'yaxis.range': eventData['yaxis.range'] || plotInstance.layout.yaxis?.range,
                        dragmode: eventData.dragmode || plotInstance.layout.dragmode,
                    };
                }
            }
            
            // 当切换到 lasso 或 select 模式时，清除所有 annotation
            if (eventData && eventData.dragmode) {
                const mode = eventData.dragmode;
                // Only reset if not already in lasso mode (to preserve selection)
                if (!lassoSelected) {
                    lassoSelected = false;
                    selectedBarcodes = [];
                    resetMarkerStyles();
                    tooltipPosition = null;
                    clearPreview();
                }
                if (mode === 'lasso' || mode === 'select') {
                    Plotly.relayout(plotInstance, {
                        annotations: [],
                    });
                    if (!lassoSelected) {
                        clickedInfo = null;
                    }
                }
            }
            // Reset axes functionality is now handled by double-click
        });

        plotInstance.on("plotly_hover", (eventData) => {
            const point = eventData.points?.[0];
            if (point) {
                const traceName = point.data.name; // "Cluster 0"

                const legendTexts = spatialDiv.querySelectorAll(".legendtext");

                legendTexts.forEach((textEl) => {
                    const label = textEl?.textContent?.trim();
                    if (label === traceName) {
                        textEl.style.fontWeight = "bold";
                        textEl.style.fill = "black";
                        textEl.parentNode.style.opacity = "1";
                    } else {
                        textEl.style.fontWeight = "normal";
                        textEl.style.fill = "#aaa";
                        textEl.parentNode.style.opacity = "0.3";
                    }
                });

                dispatch("hover", {
                    barcode: point.customdata,
                    from: "spotPlot",
                    persistent: false,
                });
            }
        });

        // Handle double click to reset view and hide annotations
        plotInstance.on("plotly_doubleclick", () => {
            console.log("Double click detected - hiding annotations and exiting lasso mode");
            
            // Set flag to prevent click event from showing annotation
            isDoubleClick = true;
            if (clickTimer) {
                clearTimeout(clickTimer);
                clickTimer = null;
            }
            
            // Clear clicked spot info to hide annotation
            clickedInfo = null;
            tooltipPosition = null;
            resetMarkerStyles();
            
            // Hide annotation - always clear annotations on double click
            annotationVisible = false;
            annotationText = "";
            annotationPos = {};
            
            // Immediately clear annotations
            Plotly.relayout(plotInstance, {
                annotations: [],
            });
            
            // Keep the flag active for a bit longer to prevent any delayed click handlers
            setTimeout(() => {
                // Clear again to make sure
                Plotly.relayout(plotInstance, {
                    annotations: [],
                });
                clickedInfo = null;
            }, 50);
            
            // Reset the double-click flag after 300ms
            setTimeout(() => {
                isDoubleClick = false;
            }, 300);
            
            // Clear all selections and reset marker opacity
            plotInstance.data.forEach((_, i) => {
                Plotly.restyle(
                    plotInstance,
                    {
                        selectedpoints: [null],
                        "selected.marker.opacity": [1],
                        "unselected.marker.opacity": [1],
                    },
                    [i],
                );
            });
            
            // Clear lasso mode and selections
            selectedBarcodes = [];
            lassoSelected = false;
            previewUrl = "";
            
            // Remove lasso paths and selection elements
            // const lassoPaths = document.querySelectorAll(".selectionlayer path");
            // const lassoCircles = document.querySelectorAll(".outline-controllers circle");
            // const lassoRects = document.querySelectorAll(".outline-controllers rect");
            
            // lassoPaths.forEach((path) => path.remove());
            // lassoCircles.forEach((circle) => circle.remove());
            // lassoRects.forEach((rect) => rect.remove());
            
            // Notify other components
            dispatch("spotClick", {
                info: null,
                lassoSelected: false,
            });
            
            let hoverInfo = {
                barcode: -1,
                from: "spotPlot",
            };
            dispatch("hover", hoverInfo);
            hoverInfo = {
                barcode: -1,
                from: "umap",
            };
            dispatch("hover", hoverInfo);

        });

        plotInstance.on("plotly_unhover", () => {
            const legendTexts = spatialDiv.querySelectorAll(".legendtext");
            legendTexts.forEach((textEl) => {
                textEl.style.fontWeight = "normal";
                textEl.style.fill = "#444";
                textEl.parentNode.style.opacity = "1";
            });

            // In lasso mode, always clear hover to allow UMAP tooltip to disappear immediately
            // Otherwise, only clear if no clickedInfo
            if (lassoSelected) {
                // Always clear in lasso mode to ensure UMAP tooltip disappears
                dispatch("hover", {
                    barcode: -1,
                    from: "spotPlot",
                    persistent: false,
                });
            } else if (!clickedInfo) {
                // Not in lasso mode, only clear if no clickedInfo
                dispatch("hover", {
                    barcode: -1,
                    from: "spotPlot",
                    persistent: false,
                });
            }
        });

        window.addEventListener("resize", () => {
            if (spatialDiv && spatialDiv.isConnected && plotInstance) {
            Plotly.Plots.resize(spatialDiv);
            }
        });
    }

    $: if (
        hoveredBarcode?.persistent &&
        hoveredBarcode.from === "spotPlot" &&
        hoveredBarcode.barcode &&
        hoveredBarcode.barcode === activeBarcode
    ) {
        applyActiveHighlight();
    }

    $: if (hoveredBarcode?.from === "umap") {
        const hovered = hoveredBarcode.barcode;

        const legendGroups = spatialDiv?.querySelectorAll(".traces") || [];

        if (!hovered || hovered === -1) {
            legendGroups.forEach((group) => {
                const textEl = group.querySelector(".legendtext");
                const pointEl = group.querySelector(".legendpoints path");

                textEl.style.fontWeight = "normal";
                textEl.style.fill = "#444";
                group.style.opacity = "1";
                if (pointEl) pointEl.style.opacity = "1";
            });
        } else if (plotInstance) {
            const match = plotInstance.data.find((trace) =>
                (trace.customdata || trace.text || []).includes(hovered),
            );
            if (match) {
                const traceName = match.name;

                legendGroups.forEach((group) => {
                    const textEl = group.querySelector(".legendtext");
                    const pointEl = group.querySelector(".legendpoints path");

                    const label = textEl?.textContent?.trim();
                    const isMatch = label === traceName;

                    textEl.style.fontWeight = isMatch ? "bold" : "normal";
                    textEl.style.fill = isMatch ? "black" : "#aaa";
                    group.style.opacity = isMatch ? "1" : "0.3";
                    if (pointEl) pointEl.style.opacity = isMatch ? "1" : "0.3";
                });
            }
        }
    }


    // Find point in spatialData (for display/coordinates)
    function findPointByBarcode(barcode) {
        if (!barcode || barcode === -1 || barcode === "") return null;
        const normalizedBarcode = `${barcode}`.trim();
        
        for (let i = 0; i < spatialData.length; i++) {
            const trace = spatialData[i];
            const barcodes = trace.customdata || trace.text || [];

            for (let j = 0; j < barcodes.length; j++) {
                const traceBarcode = `${barcodes[j]}`.trim();
                if (traceBarcode === normalizedBarcode) {
                    return {
                        traceIndex: i,
                        pointIndex: j,
                        x: trace.x[j],
                        y: trace.y[j],
                        cluster: trace.name,
                    };
                }
            }
        }
        return null;
    }
    
    // Find original cluster from originalSpatialData (for tooltip display)
    function findOriginalClusterByBarcode(barcode) {
        if (!originalSpatialData || !Array.isArray(originalSpatialData)) {
            return null;
        }
        for (let i = 0; i < originalSpatialData.length; i++) {
            const trace = originalSpatialData[i];
            if (!trace) continue;
            const barcodes = trace.customdata || trace.text || [];
            const normalizedBarcodes = Array.isArray(barcodes) ? barcodes : typeof barcodes === "string" ? [barcodes] : [];
            
            for (let j = 0; j < normalizedBarcodes.length; j++) {
                if (`${normalizedBarcodes[j]}`.trim() === `${barcode}`.trim()) {
                    return trace.name?.replace(/^cluster\s*/i, "") ?? trace.name;
                }
            }
        }
        return null;
    }

    // Handle external spot click (e.g., from table) - update plot highlight and show tooltip
    // When hoveredBarcode is persistent but activeBarcode doesn't match, it's an external click
    let lastHandledBarcode = null;
    $: if (
        hoveredBarcode?.persistent &&
        hoveredBarcode?.barcode &&
        hoveredBarcode?.barcode !== -1 &&
        plotInstance &&
        spatialDiv &&
        lastHandledBarcode !== hoveredBarcode.barcode
    ) {
        const barcode = hoveredBarcode.barcode;
        
        // Check if this is a new click (not already handled by plot's own click handler)
        if (activeBarcode !== barcode || !clickedInfo || clickedInfo.barcode !== barcode) {
            lastHandledBarcode = barcode;
            const point = findPointByBarcode(barcode);
            
            if (point) {
                // Set clickedInfo to show tooltip
                // In manual mode, use original cluster from originalSpatialData if available
                // to prevent preview updates from affecting tooltip display
                const originalCluster = findOriginalClusterByBarcode(barcode);
                clickedInfo = {
                    barcode: barcode,
                    x: point.x,
                    y: point.y,
                    cluster: originalCluster ?? point.cluster?.replace(/^cluster\s*/i, "") ?? point.cluster,
                };
                
                // Set active highlight state
                activeTraceIndex = point.traceIndex;
                activePointIndex = point.pointIndex;
                activeBarcode = barcode;
                
                // Apply highlight
                applyActiveHighlight();
                
                // Show tooltip - calculate position from plot coordinates
                (async () => {
                    if (plotInstance && spatialDiv) {
                        await tick();
                        // Convert plot coordinates to pixel coordinates
                        const plotDiv = spatialDiv;
                        const rect = plotDiv.getBoundingClientRect();
                        
                        // Get plot layout
                        const layout = plotInstance.layout;
                        const xaxis = layout.xaxis;
                        const yaxis = layout.yaxis;
                        
                        // Calculate pixel position
                        const xRange = xaxis.range[1] - xaxis.range[0];
                        const yRange = yaxis.range[1] - yaxis.range[0];
                        const xPixel = ((point.x - xaxis.range[0]) / xRange) * rect.width;
                        const yPixel = ((yaxis.range[1] - point.y) / yRange) * rect.height;
                        
                        // Convert to client coordinates
                        const clientX = rect.left + xPixel;
                        const clientY = rect.top + yPixel;
                        
                        await showTooltipAt(clientX, clientY);
                    }
                })();
            }
        } else {
            lastHandledBarcode = barcode;
        }
    }

    // Cache for lassoHover annotation to avoid unnecessary relayout calls
    let currentLassoHoverBarcode = null;
    
    // Handle tooltips in lasso mode: lassoHover takes precedence over UMAP hover
    $: if (spatialDiv && plotInstance) {
        // Check if lassoHover is active (has barcode) - this takes highest priority
        const isLassoHoverActive = lassoHover && lassoHover.barcode;
        
        if (isLassoHoverActive) {
            // Clear UMAP annotation cache when lassoHover is active
            currentUmapAnnotationBarcode = null;
            
            // Only update if barcode changed
            const lassoBarcode = lassoHover.barcode;
            if (currentLassoHoverBarcode !== lassoBarcode) {
                currentLassoHoverBarcode = lassoBarcode;
                
                // Show lassoHover tooltip (from table row hover)
                const p = findPointByBarcode(lassoBarcode);
                if (p) {
                    // Before reclustering: only show current cluster
                    // After reclustering: show original->new format
                    // Use original cluster from originalSpatialData if available to prevent preview updates from affecting display
                    const originalCluster = findOriginalClusterByBarcode(lassoBarcode);
                    const displayCluster = originalCluster ?? p.cluster?.replace(/^cluster\s*/i, "") ?? p.cluster;
                    const hasNewCluster = lassoHover.newCluster != null;
                    const displayText = hasNewCluster 
                        ? `${displayCluster}->${lassoHover.newCluster}` 
                        : displayCluster;
                    const bgColor = hasNewCluster 
                        ? clusterColorScale(lassoHover.newCluster) 
                        : clusterColorScale(displayCluster);
                    
                    Plotly.relayout(spatialDiv, {
                        annotations: [
                            {
                                x: p.x,
                                y: p.y,
                                text: displayText,
                                showarrow: true,
                                arrowhead: 1,
                                ax: 0,
                                ay: -40,
                                bgcolor: bgColor,
                                bordercolor: "",
                                borderwidth: 1,
                                layer: "above",
                            },
                        ],
                    });
                    updatePreviewCircle(p);
                } else {
                    Plotly.relayout(spatialDiv, {
                        annotations: [],
                    });
                    currentLassoHoverBarcode = null;
                }
            }
        } else {
            // lassoHover is not active, clear lassoHover annotation immediately
            if (currentLassoHoverBarcode !== null) {
                Plotly.relayout(spatialDiv, {
                    annotations: [],
                });
                currentLassoHoverBarcode = null;
            }
            
            // Show UMAP hover tooltip (only if lassoHover is not active)
            if (lassoSelected) {
                const hovered = hoveredBarcode?.barcode;
                const isFromUmap = hoveredBarcode?.from === "umap";
                
                if (isFromUmap && hovered && hovered !== -1 && hovered !== "") {
                    // Only update if barcode changed
                    if (currentUmapAnnotationBarcode !== hovered) {
                        currentUmapAnnotationBarcode = hovered;
                        
                        const p = findPointByBarcode(hovered);
                        if (p) {
                            Plotly.relayout(spatialDiv, {
                                annotations: [
                                    {
                                        x: p.x,
                                        y: p.y,
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
                            Plotly.relayout(spatialDiv, {
                                annotations: [],
                            });
                            currentUmapAnnotationBarcode = null;
                        }
                    }
                } else {
                    // Not from UMAP, or hover ended, or invalid barcode - clear tooltip immediately
                    if (currentUmapAnnotationBarcode !== null) {
                        Plotly.relayout(spatialDiv, {
                            annotations: [],
                        });
                        currentUmapAnnotationBarcode = null;
                    }
                }
            } else {
                // Not in lasso mode, clear all annotations if exists
                if (currentUmapAnnotationBarcode !== null || currentLassoHoverBarcode !== null) {
                    Plotly.relayout(spatialDiv, {
                        annotations: [],
                    });
                    currentUmapAnnotationBarcode = null;
                    currentLassoHoverBarcode = null;
                }
            }
        }
    }

    $: if (image) {
        if (image.complete) {
            imageReady = true;
        } else {
            image.onload = () => {
                imageReady = true;
            };
        }
    }

    onMount(() => {
        resizeObserver = new ResizeObserver(() => {
            if (plotInstance && spatialDiv && spatialDiv.isConnected) {
                Plotly.Plots.resize(spatialDiv);
            }
        });
        // Observe when spatialDiv becomes available
        if (spatialDiv) {
            resizeObserver.observe(spatialDiv);
        } else {
            // If not available yet, wait for it
            tick().then(() => {
                if (spatialDiv && resizeObserver) {
                    resizeObserver.observe(spatialDiv);
                }
            });
        }
    });

    onDestroy(() => {
        if (resizeObserver) {
            if (spatialDiv) {
                resizeObserver.unobserve(spatialDiv);
            }
            resizeObserver.disconnect();
        }
        // Clear click timer to prevent memory leaks
        if (clickTimer) {
            clearTimeout(clickTimer);
            clickTimer = null;
        }
    });

    function base64ToBlob(base64Data, contentType = "image/png") {
        const byteCharacters = atob(base64Data.split(",")[1]);
        const byteNumbers = new Array(byteCharacters.length)
            .fill()
            .map((_, i) => byteCharacters.charCodeAt(i));
        const byteArray = new Uint8Array(byteNumbers);
        return new Blob([byteArray], { type: contentType });
    }

    function closeTooltip() {
        clickedInfo = null;
        tooltipPosition = null;
        clearPreview(false, true, false);
        dispatch("spotClick", {
            info: null,
            lassoSelected: false,
            previewUrl: null,
        });
    }

    $: if (!clickedInfo || lassoSelected) {
        tooltipPosition = null;
    }

    $: if (plotInstance && lassoClearSignal !== lastLassoClearSignal) {
        lastLassoClearSignal = lassoClearSignal;
        const event = new CustomEvent("plotly_deselect", performLassoDeselect());
        const lassoPaths = document.querySelectorAll(
                ".selectionlayer path",
            );
            const lassoCircles = document.querySelectorAll(
                ".outline-controllers circle",
            );
            const lassoRects = document.querySelectorAll(
                ".outline-controllers rect",
            );
            lassoPaths.forEach((path) => path.remove());
            lassoCircles.forEach((circle) => circle.remove());
            lassoRects.forEach((rect) => rect.remove());
        plotInstance.dispatchEvent(event);
    }
</script>

<div class="relative w-full h-full">
    <div class="h-full" bind:this={spatialDiv}></div>

    <!-- Hint for double-click to deselect -->
    {#if lassoSelected || selectedBarcodes.length > 0 || clickedInfo}
        <div
            class="absolute left-2 z-10 bg-blue-50 px-3 py-1.5 rounded-md border border-blue-200 shadow-sm text-xs text-blue-700 pointer-events-none {previewUrl ? 'top-[322px]' : 'top-2'}"
        >
            💡 Double-click to deselect
        </div>
    {/if}

    {#if previewUrl}
        <div
            class="absolute top-2 left-2 z-10 bg-white p-1 border border-gray-300 max-w-[300px] max-h-[300px] overflow-hidden"
            bind:this={previewBox}
        >
            <img
                src={previewUrl}
                alt="Preview"
                class="max-w-full max-h-full object-contain block"
                bind:this={previewImg}
            />

            <svg
                class="absolute top-0 left-0 pointer-events-none"
                xmlns="http://www.w3.org/2000/svg"
                style="width: 100%; height: 100%;"
                bind:this={previewOverlay}
            >
                <circle
                    r="6"
                    fill="none"
                    stroke="red"
                    stroke-width="2"
                    bind:this={previewCircle}
                />
            </svg>
        </div>
    {/if}

    {#if clickedInfo && !lassoSelected && tooltipPosition}
        <div
            bind:this={tooltipEl}
            class="fixed z-50"
            style={`top:${tooltipPosition.top}px; left:${tooltipPosition.left}px;`}
        >
            <div class="relative w-[320px] max-h-[420px] overflow-hidden rounded-lg border border-gray-300 bg-white shadow-lg">
                <button
                    type="button"
                    class="absolute right-1.5 top-1.5 z-30 flex h-6 w-6 items-center justify-center rounded-full bg-white/90 text-gray-400 shadow-sm transition-all hover:bg-gray-100 hover:text-gray-600 hover:shadow-md"
                    on:click|stopPropagation={closeTooltip}
                    aria-label="Close"
                >
                    <svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                        <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
                    </svg>
                </button>
                <div class="max-h-[420px] overflow-y-auto p-4 pr-3">
                    <SpotInspection
                        {clickedInfo}
                        {availableClusters}
                        {baseApi}
                        {currentSlice}
                        {currentClusterResultId}
                        on:clusterUpdate={(event) => dispatch("clusterUpdate", event.detail)}
                    />
                </div>
            </div>
        </div>
    {/if}
</div>

<style>
    /* 确保 Plotly annotation 层显示在 lasso 白圈上层 */
    :global(.plotly .infolayer) {
        z-index: 9999 !important;
        pointer-events: none;
    }
    
    :global(.plotly .annotation-text-g) {
        pointer-events: auto;
    }
    
    /* 降低 lasso 白圈的层级 */
    :global(.plotly .outline-controllers) {
        z-index: 100 !important;
    }
</style>


