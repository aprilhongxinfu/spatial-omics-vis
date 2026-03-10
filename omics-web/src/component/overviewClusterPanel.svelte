<script>
    import * as d3 from "d3";
    import { onDestroy, tick, createEventDispatcher } from "svelte";
    import { Info } from "@lucide/svelte";

    export let clusterColorScale;
    export let clusterDistribution = [];
    export let refreshToken = 0;
    export let baseApi = "/api";
    export let currentSlice = "";
    export let currentClusterResultId = "default";
    export let allClustersMarkerGenes = {}; // Cached marker genes for all clusters
    export let cellTypeAnnotations = null; // Cell type annotations for clusters (null = not loaded, {} or object = loaded)

    const dispatch = createEventDispatcher();

    let treemapContainer;
    let svgElement = null;
    let resizeObserver;
    let selectedCluster = null;
    let clusterGenes = null;
    let isLoadingGenes = false;
    let abortController = null;
    let showCountHint = false;

    function observeResize() {
        if (!treemapContainer || typeof ResizeObserver === "undefined") return;
        resizeObserver = new ResizeObserver(() => {
            if (treemapContainer && treemapContainer.isConnected && selectedCluster === null) {
                tick().then(() => {
                    if (treemapContainer && treemapContainer.isConnected && selectedCluster === null) {
                        drawTreemap();
                    }
                });
            }
        });
        resizeObserver.observe(treemapContainer);
    }

    function getNormalizedDistribution() {
        const normalized = Array.isArray(clusterDistribution)
            ? clusterDistribution.filter(
                  (item) => item && item.cluster !== undefined && item.count,
              )
            : [];
        return normalized;
    }

    async function drawTreemap() {
        if (!treemapContainer || !treemapContainer.isConnected) return;

        await tick();
        if (!treemapContainer || !treemapContainer.isConnected) return;

        const normalized = getNormalizedDistribution();
        
        // Clear previous SVG
        d3.select(treemapContainer).selectAll("*").remove();

        if (!normalized.length) {
            d3.select(treemapContainer)
                .append("div")
                .style("display", "flex")
                .style("align-items", "center")
                .style("justify-content", "center")
                .style("height", "100%")
                .style("color", "#6b7280")
                .style("font-size", "14px")
                .text("No data");
            return;
        }

        const containerRect = treemapContainer.getBoundingClientRect();
        // Use clientWidth/clientHeight for more accurate size, fallback to getBoundingClientRect
        const width = Math.max(1, treemapContainer.clientWidth || containerRect.width || 400);
        const height = Math.max(1, treemapContainer.clientHeight || containerRect.height || 300);

        // Prepare data for D3 treemap
        const root = d3.hierarchy({
            name: "root",
            children: normalized.map(item => ({
                name: `Cluster ${item.cluster}`,
                cluster: item.cluster,
                value: Number.isFinite(item.count) ? item.count : 0
            }))
        }).sum(d => d.value)
          .sort((a, b) => (b.value || 0) - (a.value || 0));

        const treemap = d3.treemap()
            .size([width, height])
            .padding(2);

        treemap(root);

        // Create SVG with viewBox for responsive scaling
        svgElement = d3.select(treemapContainer)
            .append("svg")
            .attr("viewBox", `0 0 ${width} ${height}`)
            .attr("preserveAspectRatio", "xMidYMid meet")
            .style("display", "block")
            .style("width", "100%")
            .style("height", "100%")
            .style("max-width", "100%");

        // Create defs for clipPaths
        const defs = svgElement.append("defs");

        const cells = svgElement.selectAll("g")
            .data(root.leaves())
            .enter()
            .append("g")
            .attr("transform", d => `translate(${d.x0},${d.y0})`)
            .each(function(d, i) {
                // Add clipPath for each cell to prevent text overflow
                const cellWidth = d.x1 - d.x0;
                const cellHeight = d.y1 - d.y0;
                const clipId = `clip-cell-${i}`;
                
                defs.append("clipPath")
                    .attr("id", clipId)
                    .append("rect")
                    .attr("width", cellWidth)
                    .attr("height", cellHeight);
                
                d3.select(this).attr("clip-path", `url(#${clipId})`);
            });

        // Add rectangles
        cells.append("rect")
            .attr("width", d => d.x1 - d.x0)
            .attr("height", d => d.y1 - d.y0)
            .attr("fill", d => {
                const cluster = d.data.cluster;
                if (clusterColorScale && typeof clusterColorScale === "function") {
                    const color = clusterColorScale(cluster);
                    return color || "#999999";
                }
                return "#999999";
            })
            .attr("stroke", "#fff")
            .attr("stroke-width", 1)
            .style("cursor", "pointer")
            .on("mouseover", function(event, d) {
                d3.select(this).attr("stroke-width", 2).attr("stroke", "#333");
            })
            .on("mouseout", function(event, d) {
                d3.select(this).attr("stroke-width", 1).attr("stroke", "#fff");
            })
            .on("click", async function(event, d) {
                event.stopPropagation();
                const cluster = d.data.cluster;
                selectedCluster = cluster;
                dispatch("clusterClick", { cluster });

                // Get marker genes from cache (preloaded when cluster result is loaded)
                const clusterStr = String(cluster);
                if (allClustersMarkerGenes && allClustersMarkerGenes[clusterStr]) {
                    clusterGenes = allClustersMarkerGenes[clusterStr];
                    isLoadingGenes = false;
                } else {
                    // Fallback: fetch from API if not in cache
                    isLoadingGenes = true;
                    clusterGenes = null;
                    
                    if (abortController) {
                        abortController.abort();
                    }

                    abortController = new AbortController();
                    const currentRequestSlice = currentSlice;
                    const currentRequestClusterResultId = currentClusterResultId;

                    try {
                        const url = `${baseApi}/expression-by-cluster?cluster=${cluster}&slice_id=${currentRequestSlice}&cluster_result_id=${currentRequestClusterResultId}`;
                        const response = await fetch(url, {
                            signal: abortController.signal
                        });

                        if (abortController.signal.aborted || 
                            currentRequestSlice !== currentSlice || 
                            currentRequestClusterResultId !== currentClusterResultId) {
                            return;
                        }

                        if (response.ok) {
                            const data = await response.json();

                            if (currentRequestSlice !== currentSlice || 
                                currentRequestClusterResultId !== currentClusterResultId) {
                                return;
                            }

                            if (Array.isArray(data)) {
                                clusterGenes = data;
                            } else {
                                clusterGenes = Object.entries(data)
                                    .map(([gene, expr]) => ({ 
                                        gene, 
                                        avg_expr: expr,
                                        logFC: 0,
                                        coverage: 0
                                    }))
                                    .sort((a, b) => b.avg_expr - a.avg_expr)
                                    .slice(0, 20);
                            }
                        } else {
                            console.error("Failed to fetch cluster genes:", response.statusText);
                            clusterGenes = [];
                        }
                    } catch (error) {
                        if (error.name === 'AbortError') {
                            return;
                        }
                        console.error("Error fetching cluster genes:", error);
                        if (currentRequestSlice === currentSlice && 
                            currentRequestClusterResultId === currentClusterResultId) {
                            clusterGenes = [];
                        }
                    } finally {
                        if (currentRequestSlice === currentSlice && 
                            currentRequestClusterResultId === currentClusterResultId) {
                            isLoadingGenes = false;
                        }
                    }
                }
            });

        // Add labels with clipping to prevent overflow
        cells.each(function(d) {
            const cellWidth = d.x1 - d.x0;
            const cellHeight = d.y1 - d.y0;
            const cell = d3.select(this);
            const clusterStr = String(d.data.cluster);
            
            // Calculate font size based on cell size
            const fontSize = Math.max(10, Math.min(14, Math.min(cellWidth, cellHeight) / 8));
            const label = d.data.name;
            const value = d.value;
            const total = root.value;
            const percent = ((value / total) * 100).toFixed(1);
            
            // Create text element with tspan for multi-line
            const text = cell.append("text")
                .attr("x", cellWidth / 2)
                .attr("y", cellHeight / 2)
                .attr("text-anchor", "middle")
                .style("font-size", fontSize + "px")
                .style("fill", "#fff")
                .style("pointer-events", "none")
                .style("font-weight", "bold")
                .style("dominant-baseline", "middle");
            
            // Build lines: cluster label and value/percentage
            const lines = [label, `${value} (${percent}%)`];
            
            lines.forEach((line, i) => {
                text.append("tspan")
                    .attr("x", cellWidth / 2)
                    .attr("dy", i === 0 ? "-0.3em" : "1em")
                    .text(line);
            });
        });

        // Initialize resize observer if not already done
        if (!resizeObserver && treemapContainer) {
            observeResize();
        }
    }

    $: drawNonce =
        treemapContainer &&
        treemapContainer.isConnected &&
        clusterColorScale &&
        typeof clusterColorScale === "function" &&
        selectedCluster === null
            ? `${refreshToken}:${getNormalizedDistribution()
                  .map((item) => `${item.cluster}:${item.count}`)
                  .join("|")}`
            : null;

    $: if (drawNonce && selectedCluster === null) {
        tick().then(drawTreemap);
    }

    // Clear selection and cancel requests when slice or cluster result changes
    let lastSlice = currentSlice;
    let lastClusterResultId = currentClusterResultId;
    $: if (currentSlice !== lastSlice || currentClusterResultId !== lastClusterResultId) {
        lastSlice = currentSlice;
        lastClusterResultId = currentClusterResultId;
        // Cancel any pending request
        if (abortController) {
            abortController.abort();
            abortController = null;
        }
        // Clear selection when slice changes
        selectedCluster = null;
        clusterGenes = null;
        isLoadingGenes = false;
    }
    
    // Clear selection when marker genes cache is updated (new cluster result loaded)
    $: if (allClustersMarkerGenes && Object.keys(allClustersMarkerGenes).length > 0) {
        // Cache updated, clear current selection to allow re-selection
        if (selectedCluster && !allClustersMarkerGenes[String(selectedCluster)]) {
            selectedCluster = null;
            clusterGenes = null;
        }
    }

    // Redraw treemap when selectedCluster changes back to null
    let lastSelectedCluster = null;
    $: if (selectedCluster === null && selectedCluster !== lastSelectedCluster && treemapContainer && treemapContainer.isConnected) {
        lastSelectedCluster = selectedCluster;
        tick().then(drawTreemap);
    } else if (selectedCluster !== lastSelectedCluster) {
        lastSelectedCluster = selectedCluster;
    }

    onDestroy(() => {
        if (abortController) {
            abortController.abort();
            abortController = null;
        }
        if (svgElement) {
            d3.select(treemapContainer).selectAll("*").remove();
            svgElement = null;
        }
        if (resizeObserver && treemapContainer) {
            resizeObserver.unobserve(treemapContainer);
            resizeObserver.disconnect();
        }
    });
</script>

<div class="flex flex-col gap-2 h-full min-h-0 min-w-0">
    <div class="flex items-center justify-between text-sm font-semibold text-slate-700 flex-shrink-0">
        <div class="flex items-center gap-1 relative">
            <span>Count Distribution</span>
            {#if selectedCluster !== null}
                <span class="ml-1 px-1.5 py-0.5 rounded-full bg-slate-100 text-[11px] font-medium text-slate-700">
                    Cluster {selectedCluster}
                </span>
            {/if}
            <span
                class="ml-1 inline-flex items-center justify-center text-slate-400 hover:text-slate-600 cursor-help relative"
                aria-label="Count distribution help"
                role="button"
                tabindex="0"
                on:mouseenter={() => (showCountHint = true)}
                on:mouseleave={() => (showCountHint = false)}
            >
                <Info size={14} />

                {#if showCountHint}
                    <div
                        class="absolute z-30 mt-10 left-5 w-56 max-w-[70vw] text-[11px] leading-snug text-slate-50 bg-slate-900/95 rounded-md shadow-lg px-2.5 py-1.5"
                    >
                        Each rectangle shows how many spots fall into a cluster.
                        Click a block to view marker genes for that cluster.
                    </div>
                {/if}
            </span>
        </div>

        {#if selectedCluster !== null}
            <button
                class="text-[11px] px-2 py-1 rounded bg-slate-100 hover:bg-slate-200 text-slate-700 border border-slate-300 transition-colors"
                on:click={async () => {
                    // Cancel any pending request
                    if (abortController) {
                        abortController.abort();
                        abortController = null;
                    }
                    
                    // Clear selection state
                    selectedCluster = null;
                    clusterGenes = null;
                    isLoadingGenes = false;
                    
                    // Wait for DOM update so treemapContainer is reconnected
                    await tick();
                    
                    // Redraw the treemap
                    if (treemapContainer && treemapContainer.isConnected) {
                        await drawTreemap();
                    }
                }}
            >
                Back
            </button>
        {/if}
    </div>
    
    {#if selectedCluster === null}
        <!-- Show tree map when no cluster is selected -->
        <div 
            class="flex-1 min-h-0 w-full min-w-0" 
            bind:this={treemapContainer}
            role="button"
            tabindex="0"
        ></div>
    {:else}
        <!-- Show marker genes table when a cluster is selected -->
        <div class="flex-1 min-h-0 flex flex-col">
            {#if cellTypeAnnotations && typeof cellTypeAnnotations === "object" && cellTypeAnnotations[String(selectedCluster)] && cellTypeAnnotations[String(selectedCluster)].cell_type !== "Unknown"}
                <div class="mb-1 text-xs text-slate-500">
                    Cell Type:
                    <span class="font-medium text-slate-700">
                        {cellTypeAnnotations[String(selectedCluster)].cell_type}
                    </span>
                    {#if cellTypeAnnotations[String(selectedCluster)].score > 0}
                        <span class="text-slate-400">
                            (Score: {cellTypeAnnotations[String(selectedCluster)].score.toFixed(2)})
                        </span>
                    {/if}
                </div>
            {/if}

            {#if isLoadingGenes}
                <div class="flex-1 flex items-center justify-center text-xs text-slate-500">Loading...</div>
            {:else if clusterGenes && clusterGenes.length > 0}
                <div class="flex-1 min-h-0 overflow-auto border border-slate-200 rounded">
                    <table class="w-full text-xs">
                        <thead class="bg-slate-50 sticky top-0 z-10">
                            <tr class="border-b border-slate-200">
                                <th class="px-3 py-2 text-left font-semibold text-slate-700">Gene</th>
                                <th class="px-3 py-2 text-right font-semibold text-slate-700">logFC</th>
                                <th class="px-3 py-2 text-right font-semibold text-slate-700">Coverage</th>
                            </tr>
                        </thead>
                        <tbody class="divide-y divide-slate-100">
                            {#each clusterGenes as {gene, logFC, coverage}}
                                <tr class="hover:bg-slate-50 transition-colors">
                                    <td class="px-3 py-1.5 font-medium text-slate-900">{gene}</td>
                                    <td class="px-3 py-1.5 text-right text-slate-700 font-mono">
                                        {logFC.toFixed(3)}
                                    </td>
                                    <td class="px-3 py-1.5 text-right text-slate-700 font-mono">
                                        {(coverage * 100).toFixed(1)}%
                                    </td>
                                </tr>
                            {/each}
                        </tbody>
                    </table>
                </div>
            {:else}
                <div class="flex-1 flex items-center justify-center text-xs text-slate-500">No genes found</div>
            {/if}
        </div>
    {/if}
</div>

