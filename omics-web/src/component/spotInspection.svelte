<script>
    import { createEventDispatcher } from "svelte";
    import { Edit } from "@lucide/svelte";
    import { Tabs } from "@skeletonlabs/skeleton-svelte";

    export let clickedInfo;
    export let availableClusters;
    export let baseApi;
    export let currentSlice;
    export let currentClusterResultId = "default";

    const dispatch = createEventDispatcher();

    let group = "BasicInfo";
    let clusterEdit = false;
    let comment = ""; // 备注
    let selectedCluster = null; // 新选的 cluster
    let log;
    let expandedIndex = null;
    let clusterSelectId;
    let commentId;

    $: if (clickedInfo) {
        // group = "BasicInfo";
        clusterEdit = false;
        selectedCluster = clickedInfo.cluster;
    }

    $: clusterSelectId =
        clickedInfo?.barcode !== undefined
            ? `cluster-select-${clickedInfo.barcode}`
            : "cluster-select";
    $: commentId =
        clickedInfo?.barcode !== undefined
            ? `cluster-comment-${clickedInfo.barcode}`
            : "cluster-comment";

    async function fetchLog(barcode) {
        const res = await fetch(
            `${baseApi}/cluster-log-by-spot?cluster_result_id=${currentClusterResultId}&barcode=${barcode}`,
        );
        log = await res.json();
    }

    async function fetchExpression(barcode) {
        const res = await fetch(`${baseApi}/expression?barcode=${barcode}`);
        const expression = await res.json();
        clickedInfo.expression = Object.entries(expression)
            .filter(([, v]) => v > 0)
            .sort((a, b) => b[1] - a[1])
            .slice(0, 50);
        
        // 计算最大值用于归一化矩形条长度
        if (clickedInfo.expression && clickedInfo.expression.length > 0) {
            clickedInfo.expressionMax = Math.max(...clickedInfo.expression.map(([, v]) => v));
        }
    }
    
    // 格式化表达值
    function formatExpression(value) {
        if (value >= 1000) {
            return value.toFixed(0);
        } else if (value >= 100) {
            return value.toFixed(1);
        } else if (value >= 10) {
            return value.toFixed(2);
        } else {
            return value.toFixed(3);
        }
    }

    $: if (clickedInfo && group === "ChangeLog") {
        fetchLog(clickedInfo.barcode);
    }

    $: if (
        clickedInfo &&
        group === "GeneExpression" &&
        !clickedInfo.expression
    ) {
        fetchExpression(clickedInfo.barcode);
    }

    function changeCluster(barcode, value, comment) {
        // console.log(currentSlice, barcode, clickedInfo.cluster, value, comment);
        clusterEdit = false;
        dispatch("clusterUpdate", {
            barcode,
            newCluster: value,
            oldCluster: clickedInfo.cluster,
            comment,
        });
    }
</script>

<div class="w-full h-full flex flex-col">
    <div class="sticky top-[-1rem] -mx-4 px-4 z-20 bg-white flex items-center border-b border-gray-200 flex-shrink-0">
        <button
            class="px-3 py-2 text-xs font-medium transition-colors border-b-2 {group === 'BasicInfo' ? 'text-gray-900 border-gray-900' : 'text-gray-500 border-transparent hover:text-gray-700'}"
            on:click={() => group = 'BasicInfo'}
        >
            Basic
        </button>
        <button
            class="px-3 py-2 text-xs font-medium transition-colors border-b-2 {group === 'GeneExpression' ? 'text-gray-900 border-gray-900' : 'text-gray-500 border-transparent hover:text-gray-700'}"
            on:click={() => group = 'GeneExpression'}
        >
            Gene
        </button>
        <button
            class="px-3 py-2 text-xs font-medium transition-colors border-b-2 {group === 'ChangeLog' ? 'text-gray-900 border-gray-900' : 'text-gray-500 border-transparent hover:text-gray-700'}"
            on:click={() => group = 'ChangeLog'}
        >
            Log
        </button>
    </div>
    <div class="flex-1 overflow-y-auto min-h-0">
        <Tabs
            bind:value={group}
            onValueChange={(e) => (group = e.value)}
            class="w-full h-full"
        >
            {#snippet list()}
                <div style="display: none;"></div>
            {/snippet}
    {#snippet content()}
        <Tabs.Panel
            value="BasicInfo"
            class="overflow-y-auto px-2 py-1"
        >
            <div class="flex flex-col gap-1.5">
                {#each Object.entries(clickedInfo) as [key, spotValue]}
                    {#if key !== "expression"}
                        <div class="grid grid-cols-[68px,1fr] items-center gap-2 rounded-sm px-1.5 py-1 transition hover:bg-gray-50">
                            <span class="text-[11px] uppercase tracking-wide text-gray-400 leading-none self-center">
                                {key}
                            </span>
                            {#if key === "cluster"}
                                {#if !clusterEdit}
                                    <div class="flex items-center justify-between gap-2">
                                        <span class="text-[12px] font-medium text-gray-900 leading-none">
                                            {spotValue}
                                        </span>
                                        <button
                                            on:click={() => {
                                                clusterEdit = true;
                                            }}
                                            class="btn btn-xs flex-shrink-0"
                                            aria-label="Edit cluster"
                                        >
                                            <Edit size="15" />
                                        </button>
                                    </div>
                                {:else}
                                    <div class="space-y-1.5 rounded-sm border border-gray-200 bg-white/70 p-2 self-start">
                                        <div class="flex flex-col gap-1 sm:flex-row sm:items-center">
                                            <label
                                                class="text-xs font-medium text-gray-600 sm:w-28"
                                                for={clusterSelectId}
                                            >
                                                New Cluster
                                            </label>
                                            <select
                                                class="select select-xs flex-1"
                                                id={clusterSelectId}
                                                bind:value={selectedCluster}
                                            >
                                                {#each availableClusters as cluster}
                                                    <option value={cluster}
                                                        >{cluster}</option
                                                    >
                                                {/each}
                                            </select>
                                        </div>

                                        <div class="space-y-1">
                                            <label
                                                class="block text-xs font-medium text-gray-600"
                                                for={commentId}
                                                >Comment</label
                                            >
                                            <textarea
                                                class="textarea textarea-xs w-full"
                                                rows="3"
                                                placeholder="Why is this change needed?"
                                                id={commentId}
                                                bind:value={comment}
                                            ></textarea>
                                        </div>

                                        <div class="flex flex-wrap gap-1.5">
                                            <button
                                                type="button"
                                                class="btn btn-xs preset-filled"
                                                on:click={() => {
                                                    if (
                                                        clickedInfo?.barcode &&
                                                        selectedCluster
                                                    ) {
                                                        changeCluster(
                                                            clickedInfo.barcode,
                                                            selectedCluster,
                                                            comment,
                                                        );
                                                        clusterEdit = false;
                                                        comment = "";
                                                    }
                                                }}
                                            >
                                                Confirm
                                            </button>
                                            <button
                                                type="button"
                                                class="btn btn-xs"
                                                on:click={() => {
                                                    clusterEdit = false;
                                                    selectedCluster = clickedInfo?.cluster;
                                                    comment = "";
                                                }}
                                            >
                                                Cancel
                                            </button>
                                        </div>
                                    </div>
                                {/if}
                            {:else}
                                <div class="text-[12px] text-gray-800 leading-none break-words self-center">
                                    {#if Array.isArray(spotValue)}
                                        {spotValue.join(", ")}
                                    {:else}
                                        {spotValue}
                                    {/if}
                                </div>
                            {/if}
                        </div>
                    {/if}
                {/each}
            </div>
        </Tabs.Panel>
        <Tabs.Panel
            value="GeneExpression"
            class="overflow-y-auto px-2 py-1"
        >
            {#if clickedInfo.expression && clickedInfo.expression.length > 0}
                <div class="space-y-0.5">
                    <div class="sticky top-0 z-10 bg-white pb-1.5 pt-0.5 border-b border-gray-200">
                        <div class="grid grid-cols-[1fr_80px] gap-2 px-2 py-1.5 text-[10px] font-semibold uppercase tracking-wide text-gray-500">
                            <div>Gene</div>
                            <div class="text-right">Expression</div>
                        </div>
                    </div>
                    <div class="space-y-0.5">
                        {#each clickedInfo.expression as [gene, value], i}
                            {@const maxValue = clickedInfo.expressionMax || Math.max(...clickedInfo.expression.map(([, v]) => v))}
                            {@const percentage = maxValue > 0 ? (value / maxValue) * 100 : 0}
                            <div
                                class="group grid grid-cols-[1fr_80px] items-center gap-2 rounded-sm px-2 py-1.5 transition-colors relative overflow-hidden"
                            >
                                <!-- 背景矩形条，从右向左延伸 -->
                                <div
                                    class="absolute right-0 top-0 bottom-0 bg-blue-200 transition-all group-hover:bg-blue-300"
                                    style="width: {percentage}%; min-width: 2px;"
                                ></div>
                                <!-- 内容层 -->
                                <div class="text-[11px] font-medium text-gray-900 truncate relative z-10" title={gene}>
                                    {gene}
                                </div>
                                <div class="text-right text-[11px] font-mono text-gray-700 relative z-10">
                                    {formatExpression(value)}
                                </div>
                            </div>
                        {/each}
                    </div>
                </div>
            {:else if clickedInfo.expression === undefined}
                <div class="flex h-full items-center justify-center rounded-sm border border-dashed border-gray-200 bg-gray-50/70 px-3 py-6 text-[11px] text-gray-500">
                    Loading gene expression...
                </div>
            {:else}
                <div class="flex h-full items-center justify-center rounded-sm border border-dashed border-gray-200 bg-gray-50/70 px-3 py-6 text-[11px] text-gray-500">
                    No gene expression data available.
                </div>
            {/if}
        </Tabs.Panel>
        <Tabs.Panel
            value="ChangeLog"
            class="overflow-y-auto px-2 py-1"
        >
            {#if log?.length}
                <div class="space-y-1.5 text-xs text-gray-500">
                    {log.length} update{log.length > 1 ? "s" : ""} recorded
                </div>
                <ul class="mt-1.5 space-y-1">
                    {#each log as row, i}
                        <li class="rounded-sm border border-gray-200 px-2.5 py-1.5 text-[11px] leading-tight">
                            <div class="flex items-center justify-between">
                                <span class="font-semibold text-gray-800">
                                    {row.barcode}
                                </span>
                                <span class="text-gray-500">
                                    {row.old_cluster} → {row.new_cluster}
                                </span>
                            </div>
                            {#if row.comment}
                                <div class="mt-1 text-[10px] text-gray-500">
                                    {row.comment}
                                </div>
                            {/if}
                            <div class="mt-1 text-[10px] text-gray-400">
                                {row.updated_at}
                            </div>
                        </li>
                    {/each}
                </ul>
            {:else}
                <div class="flex h-full items-center justify-center rounded-sm border border-dashed border-gray-200 bg-gray-50/70 px-3 py-6 text-[11px] text-gray-500">
                    No change history for this spot yet.
                </div>
            {/if}
        </Tabs.Panel>
    {/snippet}
        </Tabs>
    </div>
</div>

