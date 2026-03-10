<script>
    import { onDestroy, createEventDispatcher } from "svelte";
    import OverviewLogPanel from "./overviewLogPanel.svelte";

    export let spatialData = [];
    export let clusterColorScale;
    export let allLog = [];
    export let chunkSize = 200;
    export let hoveredBarcode = { barcode: -1, from: "", persistent: false };

    const dispatch = createEventDispatcher();

    let activeTab = "spots";

    function normalizeBarcodes(trace) {
        if (!trace) return [];
        const { customdata, text, hovertext } = trace;
        if (Array.isArray(customdata) && customdata.length) return customdata;
        if (Array.isArray(text) && text.length) return text;
        if (Array.isArray(hovertext) && hovertext.length) return hovertext;
        if (typeof customdata === "string") return [customdata];
        if (typeof text === "string") return [text];
        if (typeof hovertext === "string") return [hovertext];
        return [];
    }

    function flattenSpots(data) {
        if (!Array.isArray(data) || !data.length) return [];
        const entries = [];
        data.forEach((trace, traceIndex) => {
            const barcodes = normalizeBarcodes(trace);
            const xs = Array.isArray(trace?.x) ? trace.x : [];
            const ys = Array.isArray(trace?.y) ? trace.y : [];
            barcodes.forEach((rawBarcode, idx) => {
                if (rawBarcode == null) return;
                const barcode = `${rawBarcode}`.trim();
                if (!barcode) return;
                const cluster =
                    trace?.name ??
                    trace?.cluster ??
                    `Cluster ${traceIndex + 1}`;
                entries.push({
                    id: `${traceIndex}:${idx}:${barcode}`,
                    index: entries.length + 1,
                    barcode,
                    cluster,
                });
            });
        });
        return entries;
    }

    $: flatSpots = flattenSpots(spatialData);
    $: totalSpots = flatSpots.length;

    let visibleCount = chunkSize;
    let resizeObserver;
    let scrollContainer;

    $: visibleSpots = flatSpots.slice(0, visibleCount);

    function loadMore() {
        if (visibleCount < totalSpots) {
            visibleCount = Math.min(totalSpots, visibleCount + chunkSize);
        }
    }

    function handleScroll(event) {
        const el = event.currentTarget;
        if (!el) return;
        const threshold = 64;
        if (el.scrollTop + el.clientHeight >= el.scrollHeight - threshold) {
            loadMore();
        }
    }

    function getClusterColor(cluster) {
        if (!cluster || typeof clusterColorScale !== "function") return "";
        try {
            return clusterColorScale(cluster) ?? "";
        } catch (err) {
            console.warn("[ClusterSpotLogPanel] color error:", err);
            return "";
        }
    }

    function handleSpotClick(spot, event) {
        if (!spot || !spot.barcode) return;
        
        const spotInfo = {
            barcode: spot.barcode,
            cluster: spot.cluster?.replace(/^cluster\s*/i, "") ?? spot.cluster,
        };
        
        // Dispatch spotClick event similar to plot click
        dispatch("spotClick", {
            info: spotInfo,
            lassoSelected: false,
            previewUrl: null,
            pointer: {
                clientX: event?.clientX ?? 0,
                clientY: event?.clientY ?? 0,
            },
        });
    }

    function isSpotSelected(spot) {
        if (!spot || !spot.barcode) return false;
        const selected = hoveredBarcode?.barcode;
        if (selected === undefined || selected === null) return false;
        return `${selected}`.trim() === `${spot.barcode}`.trim() && hoveredBarcode?.persistent;
    }

    function tabClass(tab) {
        const isActive = activeTab === tab;
        const base =
            "inline-flex items-center gap-1 rounded px-2 py-1 text-[11px] font-medium transition";
        const inactive =
            "bg-transparent text-slate-500 hover:bg-slate-100 hover:text-slate-700";
        const active =
            "bg-slate-700 text-white shadow-sm";
        return `${base} ${isActive ? active : inactive}`;
    }

    $: {
        visibleCount = chunkSize;
    }

    onDestroy(() => {
        if (resizeObserver && scrollContainer) {
            resizeObserver.unobserve(scrollContainer);
        }
    });
</script>

<div class="flex flex-col h-full min-h-0">
    <!-- Compact tab bar -->
    <div class="flex items-center gap-1 px-2 py-1.5 bg-slate-50 border-b border-slate-200">
        <button
            type="button"
            class={tabClass("spots")}
            on:click={() => (activeTab = "spots")}
        >
            Spots <span class="opacity-70">({totalSpots})</span>
        </button>
        <button
            type="button"
            class={tabClass("log")}
            on:click={() => (activeTab = "log")}
        >
            Log <span class="opacity-70">({allLog?.length ?? 0})</span>
        </button>
    </div>

    {#if activeTab === "spots"}
        <div
            class="flex-1 min-h-0 overflow-auto bg-white"
            bind:this={scrollContainer}
            on:scroll={handleScroll}
        >
            {#if visibleSpots.length === 0}
                <div class="flex h-full items-center justify-center text-[11px] text-slate-500">
                    No spot information available.
                </div>
            {:else}
                <table class="table-auto w-full text-[11px]">
                    <thead class="sticky top-0 bg-slate-100 text-slate-600">
                        <tr>
                            <th class="px-2 py-1 text-left w-10 font-semibold">#</th>
                            <th class="px-2 py-1 text-left font-semibold">Barcode</th>
                            <th class="px-2 py-1 text-left font-semibold w-16">Cluster</th>
                        </tr>
                    </thead>
                    <tbody>
                        {#each visibleSpots as spot (spot.id)}
                            {@const isSelected = isSpotSelected(spot)}
                            <tr
                                class="border-b border-slate-100/50 transition-colors cursor-pointer"
                                class:bg-blue-50={isSelected}
                                class:hover:bg-slate-50={!isSelected}
                                class:hover:bg-blue-100={isSelected}
                                on:click={(e) => handleSpotClick(spot, e)}
                            >
                                <td class="px-2 py-1 text-slate-400 tabular-nums">{spot.index}</td>
                                <td class="px-2 py-1 font-mono text-slate-700 truncate max-w-[120px]" title={spot.barcode}>
                                    {spot.barcode}
                                </td>
                                <td
                                    class="px-2 py-1 font-medium"
                                    style={`color:${getClusterColor(spot.cluster)};`}
                                >
                                    {spot.cluster}
                                </td>
                            </tr>
                        {/each}
                        {#if visibleCount < totalSpots}
                            <tr>
                                <td colspan="3" class="px-2 py-1.5 text-center text-slate-400 text-[10px]">
                                    Loading more...
                                </td>
                            </tr>
                        {/if}
                    </tbody>
                </table>
            {/if}
        </div>
    {:else}
        <div class="flex-1 min-h-0 overflow-auto bg-white">
            <OverviewLogPanel {allLog} {hoveredBarcode} {spatialData} {clusterColorScale} on:spotClick={(e) => dispatch("spotClick", e.detail)} />
        </div>
    {/if}
</div>


