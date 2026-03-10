<script>
    import { onMount, onDestroy, tick, createEventDispatcher } from "svelte";
    import Plotly from "plotly.js-dist-min";
    
    const dispatch = createEventDispatcher();
    
    // Helper function to observe DOM element resize
    function observeResize(dom, callback) {
        if (!dom) return;
        
        const resizeObserver = new ResizeObserver(() => {
            callback();
        });
        
        resizeObserver.observe(dom);
        
        return () => {
            resizeObserver.disconnect();
        };
    }

    export let data; // Deconvolution data from backend
    export let selectedCellTypes = []; // Selected cell types (max 6)
    export let spatialData; // Spatial data for coordinate mapping

    const MAX_SELECTED_CELL_TYPES = 6;

    // Color palette for cell types
    const deconvolutionColorPalette = [
        "#8dd3c7",
        "#ffffb3",
        "#bebada",
        "#fb8072",
        "#80b1d3",
        "#fdb462",
        "#b3de69",
        "#fccde5",
        "#d9d9d9",
        "#bc80bd",
        "#ccebc5",
        "#ffed6f",
        "#a6cee3",
        "#fdbf6f",
        "#cab2d6",
        "#fb9a99",
        "#e31a1c",
        "#1f78b4",
        "#33a02c",
        "#ff7f00",
    ];

    function getColorForCellType(index) {
        // Only use first 6 colors for selected cell types
        const maxColors = 6;
        return deconvolutionColorPalette[index % maxColors];
    }

    let hierarchyDiv;
    let chartDiv;
    let pieCanvas;
    
    // Color mapping: celltype name -> color index (to maintain stable colors)
    let cellTypeColorMap = new Map();

    // Helper function to calculate total proportion for a coarse cell type
    function calculateCoarseTypeProportion(coarseNode, data) {
        let totalProportion = 0;
        if (data.merge_map && data.merge_map[coarseNode.name]) {
            const fineTypes = data.merge_map[coarseNode.name];
            const fineIndices = fineTypes
                .map((ft) => data.original_cell_types?.indexOf(ft) ?? -1)
                .filter((idx) => idx >= 0);

            if (data.original_proportions) {
                data.original_proportions.forEach((spotProps) => {
                    fineIndices.forEach((idx) => {
                        if (idx < spotProps.length) {
                            totalProportion += spotProps[idx] || 0;
                        }
                    });
                });
            }
        }
        return totalProportion;
    }

    // Initialize default selection when data loads - select top 6 by proportion
    $: if (data && data.hierarchy && selectedCellTypes.length === 0) {
        const coarseNodes = data.hierarchy.filter(node => node.type === "coarse");
        
        // Calculate proportion for each coarse type
        const coarseTypesWithProportion = coarseNodes.map(node => ({
            name: node.name,
            proportion: calculateCoarseTypeProportion(node, data)
        }));
        
        // Sort by proportion (descending) and take top 6
        const topCoarseTypes = coarseTypesWithProportion
            .sort((a, b) => b.proportion - a.proportion)
            .slice(0, MAX_SELECTED_CELL_TYPES)
            .map(item => item.name);
        
        if (topCoarseTypes.length > 0) {
            selectedCellTypes = topCoarseTypes;
            // Initialize color mapping for default selections
            topCoarseTypes.forEach((name, idx) => {
                cellTypeColorMap.set(name, idx);
            });
            dispatch("cellTypesChange", selectedCellTypes);
        }
    }

    // Ensure every selected cell type has a color mapping, even if selection comes from parent
    $: if (selectedCellTypes && selectedCellTypes.length > 0) {
        const usedIndices = new Set(cellTypeColorMap.values());
        let nextIndex = 0;
        selectedCellTypes.forEach((name) => {
            if (!cellTypeColorMap.has(name)) {
                // Find first free color index
                while (usedIndices.has(nextIndex) && nextIndex < MAX_SELECTED_CELL_TYPES) {
                    nextIndex += 1;
                }
                cellTypeColorMap.set(name, nextIndex % MAX_SELECTED_CELL_TYPES);
                usedIndices.add(nextIndex % MAX_SELECTED_CELL_TYPES);
                nextIndex += 1;
            }
        });
    }

    // Reactive: redraw when selectedCellTypes or data changes
    $: if (data && hierarchyDiv && chartDiv && pieCanvas) {
        tick().then(() => {
            renderHierarchyChart(data, hierarchyDiv);
            renderSpatialChart(data, chartDiv, pieCanvas);
        });
    }
    
    // Also redraw both charts when selectedCellTypes changes (even if data hasn't changed)
    $: if (data && hierarchyDiv && chartDiv && pieCanvas && selectedCellTypes) {
        tick().then(() => {
            renderHierarchyChart(data, hierarchyDiv);
            renderSpatialChart(data, chartDiv, pieCanvas);
        });
    }

    // Render custom hierarchy chart using SVG
    async function renderHierarchyChart(data, divElement) {
        if (!data || !divElement || !data.hierarchy) return;

        await tick();

        const hierarchy = data.hierarchy || [];
        if (hierarchy.length === 0) return;

        // Clear the div
        divElement.innerHTML = "";

        // Get container dimensions
        const rect = divElement.getBoundingClientRect();
        const width = rect.width || 400;
        const height = rect.height || 500;
        const padding = 10;
        const leftColumnWidth = width * 0.4; // 40% for level 1
        const rightColumnX = leftColumnWidth; // No gap, directly adjacent
        const rightColumnWidth = width - rightColumnX - padding;
        const availableHeight = height - padding * 2;

        // Create SVG
        const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
        svg.setAttribute("width", "100%");
        svg.setAttribute("height", "100%");
        svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
        svg.style.cursor = "pointer";

        // Calculate values for all coarse nodes
        const coarseNodes = hierarchy.filter((node) => node.type === "coarse");
        const coarseData = [];
        let totalCoarseValue = 0;

        coarseNodes.forEach((coarseNode) => {
            // Calculate value for coarse type
            let coarseValue = 0;
            if (data.merge_map && data.merge_map[coarseNode.name]) {
                const fineTypes = data.merge_map[coarseNode.name];
                const fineIndices = fineTypes
                    .map((ft) => data.original_cell_types?.indexOf(ft) ?? -1)
                    .filter((idx) => idx >= 0);

                if (data.original_proportions) {
                    data.original_proportions.forEach((spotProps) => {
                        fineIndices.forEach((idx) => {
                            if (idx < spotProps.length) {
                                coarseValue += spotProps[idx] || 0;
                            }
                        });
                    });
                }
            }
            totalCoarseValue += coarseValue;
            coarseData.push({
                node: coarseNode,
                value: coarseValue,
            });
        });

        // Calculate positions for coarse nodes (level 1) - height proportional to value
        const nodeData = [];
        let currentY = padding;

        coarseData.forEach(({ node: coarseNode, value: coarseValue }) => {
            const coarseHeight =
                totalCoarseValue > 0
                    ? (coarseValue / totalCoarseValue) * availableHeight
                    : availableHeight / coarseData.length;

            // Store coarse node data
            nodeData.push({
                name: coarseNode.name,
                type: "coarse",
                x: padding,
                y: currentY,
                width: leftColumnWidth - padding * 2,
                height: coarseHeight,
                value: coarseValue,
            });

            // Calculate fine nodes (level 2) for this coarse node
            if (coarseNode.children && coarseNode.children.length > 0) {
                const fineNodes = coarseNode.children.filter(
                    (child) => child.type === "fine"
                );
                const fineData = [];
                let totalFineValue = 0;

                fineNodes.forEach((fineNode) => {
                    // Calculate value for fine type
                    let fineValue = 0;
                    const typeIdx =
                        data.original_cell_types?.indexOf(fineNode.name) ?? -1;
                    if (typeIdx >= 0 && data.original_proportions) {
                        data.original_proportions.forEach((spotProps) => {
                            if (typeIdx < spotProps.length) {
                                fineValue += spotProps[typeIdx] || 0;
                            }
                        });
                    }
                    totalFineValue += fineValue;
                    fineData.push({
                        node: fineNode,
                        value: fineValue,
                    });
                });

                // Position fine nodes - height proportional to value, total height = coarseHeight
                let fineCurrentY = currentY;
                fineData.forEach(({ node: fineNode, value: fineValue }) => {
                    const fineHeight =
                        totalFineValue > 0
                            ? (fineValue / totalFineValue) * coarseHeight
                            : coarseHeight / fineData.length;

                    // Store fine node data
                    nodeData.push({
                        name: fineNode.name,
                        type: "fine",
                        x: rightColumnX,
                        y: fineCurrentY,
                        width: rightColumnWidth - padding,
                        height: fineHeight,
                        value: fineValue,
                        parentName: coarseNode.name,
                    });

                    fineCurrentY += fineHeight;
                });
            }

            currentY += coarseHeight;
        });

        // Draw nodes
        nodeData.forEach((node) => {
            const isSelected = selectedCellTypes.includes(node.name);
            // Use color mapping to maintain stable colors
            const colorIndex = cellTypeColorMap.get(node.name);
            const color = isSelected && colorIndex !== undefined
                ? getColorForCellType(colorIndex)
                : "#d3d3d3";

            // Create group for node
            const nodeGroup = document.createElementNS(
                "http://www.w3.org/2000/svg",
                "g"
            );
            nodeGroup.setAttribute("data-name", node.name);
            nodeGroup.style.cursor = "pointer";

            // Rectangle
            const rect = document.createElementNS(
                "http://www.w3.org/2000/svg",
                "rect"
            );
            rect.setAttribute("x", node.x);
            rect.setAttribute("y", node.y);
            rect.setAttribute("width", node.width);
            rect.setAttribute("height", node.height);
            rect.setAttribute("fill", color);
            rect.setAttribute("stroke", "#fff");
            rect.setAttribute("stroke-width", "1");
            rect.setAttribute("rx", "2");
            nodeGroup.appendChild(rect);

            // Text - always show, but adjust based on height
            if (node.height >= 8) { // Minimum height to show any text
                const text = document.createElementNS(
                    "http://www.w3.org/2000/svg",
                    "text"
                );
                text.setAttribute("x", node.x + node.width / 2);
                text.setAttribute("y", node.y + node.height / 2);
                text.setAttribute("text-anchor", "middle");
                text.setAttribute("dominant-baseline", "middle");
                
                // Calculate font size based on height, with minimum size
                const minFontSize = node.type === "coarse" ? 8 : 7;
                const maxFontSize = node.type === "coarse" ? 12 : 10;
                const fontSize = Math.max(
                    minFontSize,
                    Math.min(maxFontSize, node.height * 0.4)
                );
                text.setAttribute("font-size", fontSize);
                text.setAttribute(
                    "font-weight",
                    node.type === "coarse" ? "bold" : "normal"
                );
                text.setAttribute("fill", "#333");
                
                // Truncate text based on available width and height
                let displayName = node.name;
                if (node.height < 15) {
                    // Very small: show first few characters only
                    const maxChars = Math.floor(node.width / (fontSize * 0.6));
                    if (displayName.length > maxChars && maxChars > 0) {
                        displayName = displayName.substring(0, Math.max(1, maxChars - 1)) + "...";
                    }
                } else if (node.height < 25) {
                    // Small: truncate if too long
                    const maxChars = Math.floor(node.width / (fontSize * 0.5));
                    if (displayName.length > maxChars && maxChars > 0) {
                        displayName = displayName.substring(0, Math.max(3, maxChars - 1)) + "...";
                    }
                } else {
                    // Normal: truncate if very long
                    if (displayName.length > 20) {
                        displayName = displayName.substring(0, 17) + "...";
                    }
                }
                
                // Show value only if height is sufficient
                text.textContent =
                    node.height >= 30
                        ? `${displayName} (${node.value.toFixed(2)})`
                        : displayName;
                nodeGroup.appendChild(text);
            }

            // Click handler
            nodeGroup.addEventListener("click", (e) => {
                e.stopPropagation();
                handleCellTypeClick(node.name);
            });

            // Hover effect
            nodeGroup.addEventListener("mouseenter", () => {
                rect.setAttribute("opacity", "0.8");
            });
            nodeGroup.addEventListener("mouseleave", () => {
                rect.setAttribute("opacity", "1");
            });

            svg.appendChild(nodeGroup);
        });

        divElement.appendChild(svg);
    }

    // Handle cell type click
    function handleCellTypeClick(clickedName) {
        const currentIndex = selectedCellTypes.indexOf(clickedName);
        if (currentIndex >= 0) {
            // Already selected, remove it (toggle off - turn gray)
            selectedCellTypes = selectedCellTypes.filter(
                (ct) => ct !== clickedName
            );
            // Remove from color map (but don't reassign other colors)
            cellTypeColorMap.delete(clickedName);
            console.log("Deselected cell type:", clickedName, "Remaining:", selectedCellTypes);
        } else {
            // Not selected, check if we can add it (toggle on - add color)
            if (selectedCellTypes.length >= MAX_SELECTED_CELL_TYPES) {
                // Already at max, show alert and don't add
                alert(
                    `Maximum ${MAX_SELECTED_CELL_TYPES} cell types can be selected. Please deselect one first.`
                );
                return;
            } else {
                // Add the new selection at the end
                selectedCellTypes = [...selectedCellTypes, clickedName];
                // Assign a color index: find the first available index (0-5)
                const usedIndices = new Set(Array.from(cellTypeColorMap.values()));
                let newColorIndex = 0;
                for (let i = 0; i < MAX_SELECTED_CELL_TYPES; i++) {
                    if (!usedIndices.has(i)) {
                        newColorIndex = i;
                        break;
                    }
                }
                cellTypeColorMap.set(clickedName, newColorIndex);
                console.log("Selected cell type:", clickedName, "Color index:", newColorIndex, "Total:", selectedCellTypes);
            }
        }
        // Dispatch event to parent and trigger re-render
        dispatch("cellTypesChange", selectedCellTypes);
        // Force re-render of both charts to update colors and pie charts
        if (data && hierarchyDiv && chartDiv && pieCanvas) {
            tick().then(() => {
                renderHierarchyChart(data, hierarchyDiv);
                renderSpatialChart(data, chartDiv, pieCanvas);
            });
        }
    }

    // Helper function to convert Plotly coordinates to pixel coordinates
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

            // Convert data coordinates to plot area coordinates
            const xInPlotArea =
                ((plotlyX - xRange[0]) / xRangeSize) * plotAreaWidth;
            const yInPlotArea =
                ((plotlyY - yRange[0]) / yRangeSize) * plotAreaHeight;

            // For reversed y-axis (yRange[0] > yRange[1]), we need to flip
            // pixelX and pixelY are relative to the Plotly div's top-left corner
            const pixelX = plotLeft + xInPlotArea;
            const pixelY = plotTop + (plotAreaHeight - yInPlotArea); // Flip for reversed y-axis

            return { x: pixelX, y: pixelY };
        } catch (error) {
            console.error("Error in plotlyToPixelCoords:", error);
            return null;
        }
    }

    async function renderSpatialChart(data, divElement, canvasElement) {
        if (!data || !divElement || !canvasElement || !spatialData) return;
        Plotly.purge(divElement);

        await tick();

        const cellTypes = data.cell_types || [];
        const spots = data.spots || [];
        const proportions = data.proportions || [];

        if (cellTypes.length === 0 || spots.length === 0) return;

        // Filter by selected cell types - only show selected types
        let filteredCellTypes = [];
        let filteredProportions = [];

        if (selectedCellTypes.length > 0) {
            // Helper function to find node in hierarchy
            function findNodeInHierarchy(nodes, name) {
                for (const node of nodes) {
                    if (node.name === name) return node;
                    if (node.children) {
                        const found = findNodeInHierarchy(node.children, name);
                        if (found) return found;
                    }
                }
                return null;
            }

            // Build proportions for selected types (coarse and fine can be mixed)
            const selectedProportions = [];
            const validSelectedTypes = [];

            selectedCellTypes.forEach((selectedType) => {
                const node = findNodeInHierarchy(data.hierarchy || [], selectedType);
                if (!node) return;

                if (
                    node.type === "coarse" &&
                    data.merge_map &&
                    data.merge_map[selectedType]
                ) {
                    // Coarse type: aggregate all fine-grained children
                    const fineTypes = data.merge_map[selectedType];
                    const fineIndices = fineTypes
                        .map(
                            (ft) =>
                                data.original_cell_types?.indexOf(ft) ?? -1
                        )
                        .filter((idx) => idx >= 0);

                    if (fineIndices.length > 0 && data.original_proportions) {
                        validSelectedTypes.push(selectedType);
                        // Calculate aggregated proportions for this coarse type
                        const aggregatedProportions =
                            data.original_proportions.map((spotProps) => {
                                return fineIndices.reduce((sum, idx) => {
                                    return sum + (spotProps[idx] || 0);
                                }, 0);
                            });
                        selectedProportions.push(aggregatedProportions);
                    }
                } else if (node.type === "fine") {
                    // Fine type: use original proportions
                    const typeIdx =
                        data.original_cell_types?.indexOf(selectedType) ?? -1;
                    if (typeIdx >= 0 && data.original_proportions) {
                        validSelectedTypes.push(selectedType);
                        const fineProportions = data.original_proportions.map(
                            (spotProps) => {
                                return spotProps[typeIdx] || 0;
                            }
                        );
                        selectedProportions.push(fineProportions);
                    }
                }
            });

            if (validSelectedTypes.length > 0) {
                filteredCellTypes = validSelectedTypes;
                // Transpose proportions: from [type1_spots, type2_spots] to [spot1_types, spot2_types]
                filteredProportions = [];
                if (
                    selectedProportions.length > 0 &&
                    selectedProportions[0].length > 0
                ) {
                    for (
                        let spotIdx = 0;
                        spotIdx < selectedProportions[0].length;
                        spotIdx++
                    ) {
                        const spotProps = selectedProportions.map(
                            (typeProps) => typeProps[spotIdx] || 0
                        );
                        // Normalize proportions for this spot (sum should be 1.0)
                        const total = spotProps.reduce((sum, val) => sum + val, 0);
                        if (total > 0) {
                            filteredProportions.push(spotProps.map(val => val / total));
                        } else {
                            filteredProportions.push(spotProps);
                        }
                    }
                }
                console.log("Filtered cell types and proportions:", {
                    filteredCellTypes,
                    filteredProportionsLength: filteredProportions.length,
                    firstSpotProportions: filteredProportions[0],
                    colorMap: Array.from(cellTypeColorMap.entries())
                });
            }
        }
        
        // If no cell types selected, don't render pie charts
        if (filteredCellTypes.length === 0) {
            // Clear the plot
            Plotly.newPlot(divElement, [], {
                xaxis: { visible: false },
                yaxis: { visible: false },
                plot_bgcolor: "transparent",
                paper_bgcolor: "transparent",
            }, {
                responsive: true,
                displayModeBar: false,
            });
            // Clear canvas
            const ctx = canvasElement.getContext("2d");
            if (ctx) {
                ctx.clearRect(0, 0, canvasElement.width, canvasElement.height);
            }
            return;
        }

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

        // Ensure we have valid ranges
        if (
            !Number.isFinite(minX) ||
            !Number.isFinite(maxX) ||
            !Number.isFinite(minY) ||
            !Number.isFinite(maxY)
        ) {
            console.error("Invalid coordinate ranges:", {
                minX,
                maxX,
                minY,
                maxY,
            });
            return;
        }

        const padding = Math.max(
            (maxX - minX) * 0.05,
            (maxY - minY) * 0.05,
            50
        );
        minX -= padding;
        maxX += padding;
        minY -= padding;
        maxY += padding;

        // Build barcode to proportions map
        const barcodeToProportions = new Map();
        spots.forEach((barcode, idx) => {
            barcodeToProportions.set(
                String(barcode).trim(),
                filteredProportions[idx] || []
            );
        });

        // Create scatter trace with all spots (transparent markers - pie charts will be drawn on canvas)
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

        // Build hover texts
        const hoverTexts = [];
        allBarcodes.forEach((barcode) => {
            const spotProportions = barcodeToProportions.get(barcode) || [];
            if (spotProportions.length > 0) {
                const parts = [];
                filteredCellTypes.forEach((cellType, idx) => {
                    const prop = spotProportions[idx] || 0;
                    if (prop > 0.01) {
                        // Only show if > 1%
                        parts.push(
                            `${cellType}: ${(prop * 100).toFixed(1)}%`
                        );
                    }
                });
                hoverTexts.push(`<b>${barcode}</b><br>${parts.join("<br>")}`);
            } else {
                hoverTexts.push(`<b>${barcode}</b>`);
            }
        });

        // Create scatter trace
        const trace = {
            x: allX,
            y: allY,
            mode: "markers",
            type: "scatter",
            marker: {
                size: 6,
                color: "rgba(0,0,0,0)", // Transparent
                line: { width: 0 },
            },
            text: hoverTexts,
            customdata: allBarcodes,
            hovertemplate: "%{text}<extra></extra>",
            hoverinfo: "text",
            showlegend: false, // Don't show base trace in legend
        };

        // Create legend traces for cell types
        const legendTraces = filteredCellTypes.map((cellType) => {
            const colorIndex = cellTypeColorMap.get(cellType);
            return {
                x: [null], // Invisible
                y: [null], // Invisible
                type: "scatter",
                mode: "markers",
                name: cellType,
                marker: {
                    color: colorIndex !== undefined ? getColorForCellType(colorIndex) : "#d3d3d3",
                    size: 8,
                },
                showlegend: true,
            };
        });

        const layout = {
            xaxis: { 
                visible: false,
                range: [minX, maxX], 
                scaleanchor: "y", 
                scaleratio: 1 
            },
            yaxis: { 
                visible: false,
                range: [maxY, minY] // y轴反向
            },
            margin: { l: 0, r: 0, t: 0, b: 0 },
            dragmode: "zoom",
            showlegend: filteredCellTypes.length > 0,
            legend: {
                x: 1.02,
                y: 1,
                xanchor: "left",
                yanchor: "top",
            },
            plot_bgcolor: "transparent",
            paper_bgcolor: "transparent",
            hovermode: "closest",
            hoverlabel: {
                bgcolor: "rgba(0, 0, 0, 0.9)",
                bordercolor: "rgba(255, 255, 255, 0.3)",
                font: { size: 12, color: "white" }
            }
        };

        await Plotly.newPlot(divElement, [trace, ...legendTraces], layout, {
            responsive: true,
            useResizeHandler: true,
            displayModeBar: false,
            scrollZoom: true, // Enable mouse wheel zoom
        });
        
        // Ensure Plotly hover layer is above canvas
        // Plotly creates a hoverlayer div that should be above everything
        // We'll set its z-index to be higher than canvas
        setTimeout(() => {
            const hoverLayer = divElement.querySelector('.hoverlayer');
            if (hoverLayer) {
                hoverLayer.style.zIndex = '20';
            }
            // Also ensure the main plot container has proper z-index
            const plotContainer = divElement.querySelector('.plotly');
            if (plotContainer) {
                plotContainer.style.zIndex = '2';
            }
        }, 100);

        // Draw pie charts after Plotly renders
        // Wait for Plotly to fully render and layout to be ready
        divElement.on("plotly_afterplot", () => {
            setTimeout(() => {
                if (canvasElement) {
                    console.log("Drawing pie charts after plot:", {
                        filteredCellTypes,
                        filteredProportionsLength: filteredProportions.length,
                        spotsLength: spots.length,
                        colorMap: Array.from(cellTypeColorMap.entries())
                    });
                    drawPieChartsToCanvas(
                        data,
                        divElement,
                        canvasElement,
                        filteredCellTypes,
                        filteredProportions,
                        spots
                    );
                }
            }, 50);
        });

        // Also draw immediately after a short delay in case event doesn't fire
        setTimeout(() => {
            if (canvasElement) {
                drawPieChartsToCanvas(
                    data,
                    divElement,
                    canvasElement,
                    filteredCellTypes,
                    filteredProportions,
                    spots
                );
            }
        }, 200);

        // Add resize observer
        observeResize(divElement, () => {
            if (divElement && divElement.isConnected) {
                Plotly.Plots.resize(divElement);
                setTimeout(() => {
                    if (canvasElement) {
                        drawPieChartsToCanvas(
                            data,
                            divElement,
                            canvasElement,
                            filteredCellTypes,
                            filteredProportions,
                            spots
                        );
                    }
                }, 50);
            }
        });

        // Listen to Plotly's relayout event
        let relayoutTimeout = null;
        let relayoutAnimationFrame = null;
        divElement.on("plotly_relayout", () => {
            if (relayoutTimeout) {
                clearTimeout(relayoutTimeout);
            }
            if (relayoutAnimationFrame) {
                cancelAnimationFrame(relayoutAnimationFrame);
            }

            relayoutTimeout = setTimeout(() => {
                relayoutAnimationFrame = requestAnimationFrame(() => {
                    drawPieChartsToCanvas(
                        data,
                        divElement,
                        canvasElement,
                        filteredCellTypes,
                        filteredProportions,
                        spots
                    );
                });
            }, 150);
        });
    }

    function drawPieChartsToCanvas(
        data,
        plotDiv,
        canvasElement,
        cellTypes,
        proportions,
        spots
    ) {
        if (!data || !plotDiv || !canvasElement || !spatialData) {
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

        // Setup canvas - get dimensions from the Plotly div, not parent
        const plotDivRect = plotDiv.getBoundingClientRect();
        const dpr =
            typeof window !== "undefined" ? window.devicePixelRatio || 1 : 1;
        const physicalWidth = Math.floor(plotDivRect.width * dpr);
        const physicalHeight = Math.floor(plotDivRect.height * dpr);

        canvasElement.style.width = `${plotDivRect.width}px`;
        canvasElement.style.height = `${plotDivRect.height}px`;

        if (
            canvasElement.width !== physicalWidth ||
            canvasElement.height !== physicalHeight
        ) {
            canvasElement.width = physicalWidth;
            canvasElement.height = physicalHeight;
        }
        
        // Canvas is absolutely positioned over Plotly div with inset-0, so they should align
        // No need for offset adjustment

        const ctx = canvasElement.getContext("2d");
        if (!ctx) {
            console.warn("Failed to get canvas context");
            return;
        }

        ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.clearRect(0, 0, physicalWidth, physicalHeight);
        ctx.scale(dpr, dpr);

        ctx.lineWidth = 0;
        ctx.strokeStyle = "transparent";

        const radius = 3;
        
        let drawnCount = 0;
        let missingCoordsCount = 0;
        let emptySlicesCount = 0;
        let conversionFailedCount = 0;
        let outOfBoundsCount = 0;

        // Draw pie chart for each spot
        spots.forEach((barcode, spotIdx) => {
            const barcodeStr = String(barcode).trim();
            const coords = barcodeToCoords.get(barcodeStr);
            if (!coords) {
                missingCoordsCount++;
                if (spotIdx < 5) {
                    console.warn("Missing coordinates for barcode:", barcodeStr, "Available barcodes sample:", Array.from(barcodeToCoords.keys()).slice(0, 5));
                }
                return;
            }

            const spotProportions = proportions[spotIdx] || [];
            const pieSlices = cellTypes
                .map((cellType, typeIdx) => {
                    const colorIndex = cellTypeColorMap.get(cellType);
                    const color = colorIndex !== undefined 
                        ? getColorForCellType(colorIndex) 
                        : "#d3d3d3";
                    const value = spotProportions[typeIdx] || 0;
                    // Debug first few spots
                    if (spotIdx < 3 && typeIdx === 0) {
                        console.log("Pie chart debug:", {
                            barcode: barcodeStr,
                            cellType,
                            value,
                            colorIndex,
                            color,
                            allProportions: spotProportions,
                            cellTypes: cellTypes,
                            colorMap: Array.from(cellTypeColorMap.entries())
                        });
                    }
                    return {
                        cellType,
                        value: value,
                        color: color,
                    };
                })
                .filter((slice) => slice.value > 0.01); // Only show slices > 1%

            if (pieSlices.length === 0) {
                emptySlicesCount++;
                if (spotIdx < 5) {
                    console.warn("No pie slices for barcode:", barcodeStr, "proportions:", spotProportions);
                }
                return;
            }

            // Convert Plotly coordinates to pixel coordinates
            const pixelCoords = plotlyToPixelCoords(
                coords.x,
                coords.y,
                plotDiv
            );
            if (!pixelCoords) {
                conversionFailedCount++;
                // Debug: log first few failures
                if (spotIdx < 5) {
                    console.warn("Failed to convert coordinates:", {
                        barcode: barcodeStr,
                        coords,
                        hasLayout: !!plotDiv._fullLayout,
                        layout: plotDiv._fullLayout ? {
                            xaxis: plotDiv._fullLayout.xaxis?.range,
                            yaxis: plotDiv._fullLayout.yaxis?.range,
                            size: plotDiv._fullLayout._size
                        } : null
                    });
                }
                return;
            }

            // Convert Plotly coordinates to canvas coordinates
            // plotlyToPixelCoords returns coordinates relative to Plotly div's plot area
            // Since our layout has margin: 0, plot area = entire div
            // Canvas is absolutely positioned over Plotly div with inset-0, so coordinates should align directly
            const centerX = pixelCoords.x;
            const centerY = pixelCoords.y;
            
            // Get plot div rect for bounds checking
            const plotDivRect = plotDiv.getBoundingClientRect();

            // Check if coordinates are within canvas bounds (with some tolerance)
            if (
                centerX < -radius * 5 ||
                centerX > plotDivRect.width + radius * 5 ||
                centerY < -radius * 5 ||
                centerY > plotDivRect.height + radius * 5
            ) {
                outOfBoundsCount++;
                if (spotIdx < 5) {
                    console.warn("Coordinates out of bounds:", {
                        barcode: barcodeStr,
                        pixelCoords,
                        centerX,
                        centerY,
                        canvasWidth: plotDivRect.width,
                        canvasHeight: plotDivRect.height,
                        offsetX,
                        offsetY,
                        canvasRect: { left: canvasRect.left, top: canvasRect.top, width: canvasRect.width, height: canvasRect.height },
                        plotDivRect: { left: plotDivRectForCoords.left, top: plotDivRectForCoords.top, width: plotDivRectForCoords.width, height: plotDivRectForCoords.height }
                    });
                }
                return;
            }

            // Calculate total
            const total = pieSlices.reduce(
                (sum, slice) => sum + slice.value,
                0
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
                    currentAngle + sliceAngle
                );
                ctx.closePath();

                ctx.fillStyle = color || "#d3d3d3";
                ctx.fill();

                currentAngle += sliceAngle;
            });
            
            drawnCount++;
        });
        
        console.log(`Drew ${drawnCount} pie charts out of ${spots.length} spots`, {
            canvasSize: { width: plotDivRect.width, height: plotDivRect.height },
            physicalSize: { width: physicalWidth, height: physicalHeight },
            dpr,
            cellTypesCount: cellTypes.length,
            proportionsLength: proportions.length,
            spotsLength: spots.length,
            missingCoordsCount,
            emptySlicesCount,
            conversionFailedCount,
            outOfBoundsCount,
            barcodeToCoordsSize: barcodeToCoords.size,
            sampleBarcodes: Array.from(spots).slice(0, 3),
            sampleCoordsKeys: Array.from(barcodeToCoords.keys()).slice(0, 3)
        });
    }
</script>

<!-- Deconvolution Analysis: Left hierarchy chart + Right spatial pie chart -->
<div class="w-full flex gap-4" style="height: 60vh;">
    <!-- Left: Hierarchy chart for cell type selection (takes remaining space) -->
    <div class="flex-1 flex flex-col border border-gray-200 rounded-lg p-2 h-full min-w-0">
        <div class="text-xs font-semibold text-gray-700 mb-1">
            Cell Type Hierarchy
        </div>
        <div class="text-xs text-gray-500 mb-2">
            Click to select/deselect cell types to view. Maximum 6 selections.
        </div>
        <div
            bind:this={hierarchyDiv}
            class="flex-1 w-full"
        ></div>
        <!-- {#if selectedCellTypes.length > 0}
            <div class="text-xs text-gray-600 mt-2 p-2 bg-gray-50 rounded">
                <div class="font-semibold mb-1">
                    Selected ({selectedCellTypes.length}/{MAX_SELECTED_CELL_TYPES}):
                </div>
                <div class="flex flex-wrap gap-1">
                    {#each selectedCellTypes as cellType}
                        {@const colorIndex = cellTypeColorMap.get(cellType)}
                        <span
                            class="inline-flex items-center gap-1 px-2 py-0.5 bg-white border border-gray-300 rounded text-xs"
                        >
                            <span
                                style="display: inline-block; width: 12px; height: 12px; background-color: {colorIndex !== undefined ? getColorForCellType(colorIndex) : '#d3d3d3'}; border-radius: 2px;"
                            ></span>
                            {cellType}
                            <button
                                type="button"
                                class="text-gray-400 hover:text-red-600 ml-1"
                                on:click={() => {
                                    selectedCellTypes = selectedCellTypes.filter(
                                        (ct) => ct !== cellType
                                    );
                                    cellTypeColorMap.delete(cellType);
                                    dispatch("cellTypesChange", selectedCellTypes);
                                    // Force re-render of both charts
                                    if (data && hierarchyDiv && chartDiv && pieCanvas) {
                                        tick().then(() => {
                                            renderHierarchyChart(data, hierarchyDiv);
                                            renderSpatialChart(data, chartDiv, pieCanvas);
                                        });
                                    }
                                }}
                            >
                                ×
                            </button>
                        </span>
                    {/each}
                </div>
                <button
                    type="button"
                    class="text-xs text-blue-600 hover:text-blue-800 underline mt-2"
                    on:click={() => {
                        selectedCellTypes = [];
                        cellTypeColorMap.clear();
                        dispatch("cellTypesChange", selectedCellTypes);
                        // Force re-render of both charts
                        if (data && hierarchyDiv && chartDiv && pieCanvas) {
                            tick().then(() => {
                                renderHierarchyChart(data, hierarchyDiv);
                                renderSpatialChart(data, chartDiv, pieCanvas);
                            });
                        }
                    }}
                >
                    Clear All
                </button>
            </div>
        {/if} -->
    </div>
    <!-- Right: Spatial pie chart (always square, fixed on right) -->
    <div
        class="relative flex-shrink-0"
        style="aspect-ratio: 1; height: 100%;"
    >
        <div class="w-full h-full" bind:this={chartDiv}></div>
        <canvas
            class="absolute inset-0 w-full h-full"
            style="z-index: 1; pointer-events: none !important;"
            bind:this={pieCanvas}
        ></canvas>
    </div>
</div>

