<script>
    import { createEventDispatcher, onMount, onDestroy, tick } from "svelte";
    import Plotly from "plotly.js-dist-min";
    import * as d3 from "d3";

    let spatialDiv;
    let clickedInfo;
    export let spatialData;
    export let imageUrl;
    export let clusterColorScale;
    export let hoveredBarcode;
    export let lassoHover;
    export let lassoSelected = false;

    let clusterEdit = false;
    let availableClusters = [];
    let selectedCluster = null;
    let prevSpatialData;
    let comment = "";
    let image;
    const dispatch = createEventDispatcher();
    let resizeObserver;
    let selectedBarcodes = [];
    let prevSelectedBarcodes = [];
    let plotInstance = null;
    let previewUrl = "";

    // 图像加载后才可绘制图层背景
    async function loadImage(url) {
        return new Promise((resolve) => {
            const img = new Image();
            img.crossOrigin = "anonymous";
            img.src = url;
            img.onload = () => resolve(img);
        });
    }

    $: if (spatialData !== prevSpatialData) {
        prevSpatialData = spatialData;
        if (spatialData && image) {
            drawPlot();
        }
    }

    $: if (spatialData && !plotInstance) {
        selectedBarcodes = lassoSelected ? prevSelectedBarcodes : [];
        drawPlot();
    }

    $: if (hoveredBarcode.from === "umap") {
        drawPlot();
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
        image = await loadImage(imageUrl);
        const base64 = toBase64(image);
        const layout = {
            autosize: true,
            title: "Spatial Clusters",
            xaxis: { visible: false, range: [0, image.width] },
            yaxis: {
                visible: false,
                autorange: "reversed",
                // autorange: false,
                scaleanchor: "x",
                scaleratio: 1,
                range: [image.height, 0],
            },
            dragmode: false,
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

        const traces = spatialData.map((trace) => {
            const barcodes = trace.customdata || trace.text || [];
            const hoveredIndex = barcodes.indexOf(
                hoveredBarcode?.barcode ?? "",
            );

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

            return {
                ...trace,
                marker: {
                    ...trace.marker,
                    color: clusterColorScale(trace.name),
                    opacity: isHovering ? 0.2 : 1, // 非高亮点透明
                },
                name: `Cluster ${trace.name}`,
                selectedpoints:
                    isHovering && hoveredIndex !== -1
                        ? [hoveredIndex]
                        : selectedIndices,
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
                ["pan2d", "resetScale2d", "lasso2d", "select2d", "toImage"],
            ],
            responsive: true,
        });

        bindPlotEvents();
    }

    async function bindPlotEvents() {
        if (!plotInstance) return;

        plotInstance.on("plotly_selected", (eventData) => {
            (async () => {
                const lassoPaths = document.querySelectorAll(
                    ".selectionlayer path",
                );
                const lassoCircles = document.querySelectorAll(
                    ".outline-controllers circle",
                );
                lassoPaths.forEach((path) => path.remove());
                lassoCircles.forEach((circle) => circle.remove());
                clickedInfo = null;
                clusterEdit = false;
                lassoSelected = true;

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

                        // const yRange = plotInstance.layout.yaxis.range;
                        // const yTopImage = Math.max(...yRange); // 修复核心点
                        const sizex = imageObj.sizex ?? 1;
                        // const sizey = imageObj.sizey ?? 1;

                        // const scaleX = image.width / sizex;
                        // // const scaleY = image.height / sizey;

                        // const y0Sel = Math.min(y0, y1);
                        // const y1Sel = Math.max(y0, y1);
                        // const x0Sel = Math.min(x0, x1);
                        // const x1Sel = Math.max(x0, x1);

                        // const yRange = plotInstance.layout.yaxis.range;
                        // const yTopImage = Math.max(...yRange); // 图像顶部的逻辑 y 值
                        // const yBottomImage = Math.min(...yRange); // 图像底部逻辑 y 值
                        // const sizey = yTopImage - yBottomImage;
                        // const scaleY = image.height / sizey;

                        // const sx = (x0Sel - x0Image) * scaleX;
                        // const sw = (x1Sel - x0Image) * scaleX - sx;

                        // // 注意方向是图像从上到下
                        // const sy = (y0Sel - yBottomImage) * scaleY;
                        // const sh = (y1Sel - y0Sel) * scaleY;

                        // console.log("xRange:", xRange);
                        // console.log("yRange:", yRange);
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
                    }

                    const barcodes = eventData.points.map(
                        (pt) => pt.customdata,
                    );
                    selectedBarcodes = barcodes;
                    prevSelectedBarcodes = barcodes;
                    console.log("Selected barcodes:", barcodes);

                    plotInstance.data.forEach((_, i) => {
                        Plotly.restyle(
                            plotInstance,
                            {
                                "selected.marker.opacity": [1],
                                "unselected.marker.opacity": [0.2],
                            },
                            [i],
                        );
                    });

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
                    });
                }
            })();
        });

        plotInstance.on("plotly_deselect", () => {
            clickedInfo = null;
            lassoSelected = false;
            dispatch("spotClick", {
                info: clickedInfo,
                lassoSelected: lassoSelected,
            });
            clusterEdit = false;
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
            selectedBarcodes = [];
        });

        plotInstance.on("plotly_click", async (eventData) => {
            const mode = plotInstance._fullLayout.dragmode;
            clusterEdit = false;

            const point = eventData.points[0];
            const barcode = point.customdata;
            clickedInfo = { barcode, loading: true };

            selectedCluster = point.data.name;
            clickedInfo = {
                barcode,
                x: point.x,
                y: point.y,
                cluster: point.data.name.replace(/^cluster\s*/i, ""),
            };

            Plotly.relayout(spatialDiv, {
                annotations: [
                    {
                        x: point.x,
                        y: point.y,
                        text: `${barcode}`,
                        showarrow: true,
                        arrowhead: 1,
                        ax: 0,
                        ay: -40,
                        bgcolor: "white",
                        bordercolor: "",
                        borderwidth: 1,
                    },
                ],
            });

            dispatch("spotClick", {
                info: clickedInfo,
                lassoSelected: lassoSelected,
            });
        });

        plotInstance.on("plotly_relayout", (eventData) => {
            console.log("plotly_relayout triggered:", eventData);
            function approxEqual(a, b, tol = 1e-2) {
                return Math.abs(a - b) < tol;
            }

            const xRange = eventData["xaxis.range"] || [
                eventData["xaxis.range[0]"],
                eventData["xaxis.range[1]"],
            ];
            const yRange = eventData["yaxis.range"] || [
                eventData["yaxis.range[0]"],
                eventData["yaxis.range[1]"],
            ];
            if (
                eventData["xaxis.range"] &&
                eventData["xaxis.range"][0] === 0 &&
                eventData["xaxis.range"][1] === image.width &&
                eventData["yaxis.autorange"] === true
            ) {
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

                Plotly.relayout(plotInstance, {
                    dragmode: false,
                    annotations: [],
                });

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

                previewUrl = "";

                clickedInfo = null;
                lassoSelected = false;
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
            }
        });

        // const defaultXRange = [0, image.width];
        // const defaultYRange = [image.height, 0];

        // plotInstance.on("plotly_relayout", (eventData) => {
        //     const xRange = eventData["xaxis.range"];
        //     const yRange = eventData["yaxis.range"];

        //     const isResetClick =
        //         Array.isArray(xRange) &&
        //         xRange[0] === defaultXRange[0] &&
        //         xRange[1] === defaultXRange[1] &&
        //         Array.isArray(yRange) &&
        //         yRange[0] === defaultYRange[0] &&
        //         yRange[1] === defaultYRange[1];

        //     if (!isResetClick) {
        //         // 用户不是点了 reset，而是拖动或缩放 → 忽略
        //         return;
        //     }

        //     // ✅ 下面才是“真正的重置”行为
        //     Plotly.restyle(plotInstance, {
        //         selectedpoints: [null],
        //         "selected.marker.opacity": [1],
        //         "unselected.marker.opacity": [1],
        //     });

        //     Plotly.relayout(plotInstance, {
        //         dragmode: false,
        //         annotations: [],
        //     });

        //     document
        //         .querySelectorAll(
        //             ".selectionlayer path, .outline-controllers circle, .outline-controllers rect",
        //         )
        //         .forEach((el) => el.remove());

        //     clickedInfo = null;
        //     lassoSelected = false;

        //     dispatch("spotClick", {
        //         info: null,
        //         lassoSelected: false,
        //     });

        //     dispatch("hover", { barcode: -1, from: "spotPlot" });
        //     dispatch("hover", { barcode: -1, from: "umap" });
        // });

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
                });
            }
        });

        plotInstance.on("plotly_unhover", () => {
            const legendTexts = spatialDiv.querySelectorAll(".legendtext");
            legendTexts.forEach((textEl) => {
                textEl.style.fontWeight = "normal";
                textEl.style.fill = "#444";
                textEl.parentNode.style.opacity = "1";
            });

            dispatch("hover", {
                barcode: -1,
                from: "spotPlot",
            });
        });

        window.addEventListener("resize", () => {
            Plotly.Plots.resize(spatialDiv);
        });
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

    function findPointByBarcode(barcode) {
        for (let i = 0; i < spatialData.length; i++) {
            const trace = spatialData[i];
            const barcodes = trace.customdata || trace.text || [];

            const index = barcodes.indexOf(barcode);
            if (index !== -1) {
                return {
                    traceIndex: i,
                    pointIndex: index,
                    x: trace.x[index],
                    y: trace.y[index],
                    cluster: trace.name,
                };
            }
        }
        return null;
    }

    $: if (lassoHover && spatialDiv) {
        const p = findPointByBarcode(lassoHover.barcode);
        console.log(p);
        if (p) {
            Plotly.relayout(spatialDiv, {
                annotations: [
                    {
                        x: p.x,
                        y: p.y,
                        text: `${p.cluster}->${lassoHover.newCluster}`,
                        showarrow: true,
                        arrowhead: 1,
                        ax: 0,
                        ay: -40,
                        bgcolor: "white",
                        bordercolor: "",
                        borderwidth: 1,
                    },
                ],
            });
        } else {
            Plotly.relayout(spatialDiv, {
                annotations: [],
            });
        }
        // const p = spatialData.
    }

    onMount(() => {
        resizeObserver = new ResizeObserver(() => {
            if (plotInstance && spatialDiv) {
                Plotly.Plots.resize(spatialDiv);
            }
        });
        if (spatialDiv) resizeObserver.observe(spatialDiv);
    });

    onDestroy(() => {
        if (resizeObserver && spatialDiv) {
            resizeObserver.unobserve(spatialDiv);
        }
    });
</script>

<div class="relative w-full h-full">
    <div class="h-full" bind:this={spatialDiv}></div>

    {#if previewUrl}
        <div
            class="absolute top-2 left-2 z-10 bg-white p-1 border border-gray-300 max-w-[300px] max-h-[300px] overflow-auto"
        >
            <img
                src={previewUrl}
                alt="Preview"
                class="max-w-full max-h-full object-contain"
            />
        </div>
    {/if}
</div>

<style>
</style>
