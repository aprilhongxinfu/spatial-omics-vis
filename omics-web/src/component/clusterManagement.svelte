<script>
    import { createEventDispatcher } from "svelte";
    import { Plus, Minus, Trash2, GripVertical, Eye, EyeOff } from "@lucide/svelte";

    export let baseApi;
    export let currentSlice;
    export let clusteringMethods;
    export let currentMethod;
    export let n_clusters;
    export let epoch;
    export let autoSelectOnLoad = true;
    export let verticalLayout = false; // 是否使用纵向布局
    export let sortBy = "created_at"; // Sorting method
    export let sortDirection = "default"; // "default" | "asc" | "desc"
    export let manualOrder = null; // Manual order array of cluster_result_ids
    export let hiddenClusterResultIds = new Set(); // Set of hidden cluster result IDs
    export let currentModule = "clustering"; // Current module: "clustering" or "downstream"

    // 手动排序的ID数组
    let internalManualOrder = manualOrder || null;

    // 当sortBy改变时，如果存在手动排序且不是切换到custom，清除手动排序
    let lastSortBy = sortBy;
    $: {
        if (
            sortBy !== lastSortBy &&
            internalManualOrder &&
            sortBy !== "custom"
        ) {
            // 如果sortBy改变且不是切换到custom，清除手动排序，恢复自动排序
            internalManualOrder = null;
            dispatch("orderChange", { order: null });
        }
        lastSortBy = sortBy;
    }

    // 当sortBy或sortDirection改变时，触发重新排序（仅在无手动排序且不是custom时）
    $: if (sortBy && !internalManualOrder && sortBy !== "custom") {
        // 这个reactive statement确保当sortBy或sortDirection改变时，sortedClusterResults会重新计算
        sortedClusterResults =
            clusterResults.length > 0
                ? [...clusterResults].sort((a, b) => {
                      if (sortBy === "created_at") {
                          // Sort by creation time (newest first)
                          const timeA = new Date(
                              a.created_at || a.updated_at || 0,
                          ).getTime();
                          const timeB = new Date(
                              b.created_at || b.updated_at || 0,
                          ).getTime();
                          return timeB - timeA; // Newest first
                      }
                      // Sort by metrics (direction controlled by sortDirection; default keeps PAS special rule)
                      const metricsA = a.metrics || {};
                      const metricsB = b.metrics || {};
                      const valueA = metricsA[sortBy];
                      const valueB = metricsB[sortBy];

                      // Handle null/undefined values
                      if (valueA === null || valueA === undefined) return 1; // Move to end
                      if (valueB === null || valueB === undefined) return -1; // Move to beginning

                      if (sortDirection === "asc") {
                          return valueA - valueB;
                      }
                      if (sortDirection === "desc") {
                          return valueB - valueA;
                      }
                      if (sortBy === "pas") {
                          return valueA - valueB; // Lower values first
                      }
                      return valueB - valueA; // Higher values first
                  })
                : [];
    }

    // 当有手动排序时，使用手动排序
    $: if (internalManualOrder && clusterResults.length > 0) {
        const orderMap = new Map();
        internalManualOrder.forEach((id, index) => {
            orderMap.set(id, index);
        });
        sortedClusterResults = [...clusterResults].sort((a, b) => {
            const indexA = orderMap.get(a.cluster_result_id) ?? 9999;
            const indexB = orderMap.get(b.cluster_result_id) ?? 9999;
            return indexA - indexB;
        });
    }

    // 当manualOrder prop改变时，更新内部状态
    $: if (manualOrder !== internalManualOrder) {
        internalManualOrder = manualOrder;
    }

    const dispatch = createEventDispatcher();

    let showModal = false;
    let clusterResults = [];
    let sortedClusterResults = [];
    let loadingResults = false;
    let selectedResultId = null;
    let currentClusterResult = null;
    let imageErrors = new Set(); // Track which images failed to load
    let scrollContainer;

    // 上次已加载 cluster-results 的 slice，避免同一 slice 被重复请求
    let lastLoadedSlice = null;

    // 用于新聚类的参数
    let newMethod = currentMethod || "GraphST";
    let newNClusters = n_clusters || 7;
    let newEpoch = epoch || 500;

    async function loadClusterResults({
        autoSelectFirst = autoSelectOnLoad,
    } = {}) {
        if (!currentSlice) return;
        loadingResults = true;
        // 通知父组件：聚类结果列表开始加载
        dispatch("loadingChange", { loading: true });
        try {
            const res = await fetch(
                `${baseApi}/cluster-results?slice_id=${currentSlice}`,
            );
            const data = await res.json();

            // 保留已有结果的 metrics 数据（避免被覆盖）
            // 优先使用已有计算结果，即使后端返回了 null
            const existingResultsMap = new Map();
            clusterResults.forEach((r) => {
                if (
                    r.metrics &&
                    Object.values(r.metrics).some(
                        (v) => v !== null && v !== undefined,
                    )
                ) {
                    existingResultsMap.set(r.cluster_result_id, r.metrics);
                }
            });

            // 合并新数据和已有的 metrics
            // 如果后端返回的 metrics 都是 null，但前端已有计算结果，则保留前端的
            clusterResults = data.map((result) => {
                const existingMetrics = existingResultsMap.get(
                    result.cluster_result_id,
                );
                const backendMetrics = result.metrics || {};
                const backendAllNull = Object.values(backendMetrics).every(
                    (v) => v === null || v === undefined,
                );

                // 如果后端 metrics 都是 null，但前端有计算结果，使用前端的
                if (existingMetrics && backendAllNull) {
                    return {
                        ...result,
                        metrics: existingMetrics,
                    };
                }
                // 如果后端有 metrics，使用后端的（后端数据更权威）
                if (!backendAllNull) {
                    return result;
                }
                // 如果都没有，返回原样
                return result;
            });

            // 如果有手动排序，过滤掉已删除的结果，保留有效的手动排序
            if (internalManualOrder) {
                const validIds = new Set(
                    clusterResults.map((r) => r.cluster_result_id),
                );
                internalManualOrder = internalManualOrder.filter((id) =>
                    validIds.has(id),
                );
                // 如果有新结果不在手动排序中，添加到末尾
                clusterResults.forEach((result) => {
                    if (
                        !internalManualOrder.includes(result.cluster_result_id)
                    ) {
                        internalManualOrder.push(result.cluster_result_id);
                    }
                });
                dispatch("orderChange", { order: internalManualOrder });
            }

            dispatch("clusterResultsLoaded", { results: clusterResults });

            if (autoSelectFirst && clusterResults.length) {
                selectedResultId = clusterResults[0].cluster_result_id;
                currentClusterResult = clusterResults[0];
                dispatch("selectClusterResult", {
                    result: clusterResults[0],
                    cluster_result_id: clusterResults[0].cluster_result_id,
                    method: clusterResults[0].method,
                    n_clusters: clusterResults[0].n_clusters,
                    epoch: clusterResults[0].epoch,
                    autoSelected: true,
                });
            }
        } catch (error) {
            console.error("Error loading cluster results:", error);
            clusterResults = [];
            dispatch("clusterResultsLoaded", { results: [] });
        } finally {
            loadingResults = false;
            // 通知父组件：聚类结果列表加载结束
            dispatch("loadingChange", { loading: false });
        }
    }

    function openNewForm() {
        newMethod = currentMethod || "GraphST";
        newNClusters = n_clusters || 7;
        newEpoch = epoch || 500;
        showModal = true;
    }

    function closeNewForm() {
        showModal = false;
    }

    function handleRunCluster() {
        dispatch("runCluster", {
            method: newMethod,
            n_clusters: Number(newNClusters) || 7,
            epoch: Number(newEpoch) || 500,
        });
        closeNewForm();
    }

    function handleSelectResult(
        result,
        { autoSelected = false, isPreview = false } = {},
    ) {
        // In downstream analysis mode, if clicking the already selected result, do nothing
        if (currentModule === "downstream" && selectedResultId === result.cluster_result_id && !autoSelected) {
            return; // No action when clicking already selected result in downstream mode
        }
        
        // If clicking the already selected result (in other modes), deselect it
        // BUT: if isPreview is false (opening detail page), always open it even if already selected
        if (selectedResultId === result.cluster_result_id && !autoSelected && isPreview) {
            selectedResultId = null;
            currentClusterResult = null;
            dispatch("selectClusterResult", {
                result: null,
                cluster_result_id: null,
                method: null,
                n_clusters: null,
                epoch: null,
                autoSelected: false,
                isPreview,
            });
            return;
        }

        selectedResultId = result.cluster_result_id;
        currentClusterResult = result;
        dispatch("selectClusterResult", {
            result,
            cluster_result_id: result.cluster_result_id,
            method: result.method,
            n_clusters: result.n_clusters,
            epoch: result.epoch,
            autoSelected,
            isPreview,
        });
    }

    // Load cluster results only when slice actually changes (avoid repeated calls for same slice)
    $: if (currentSlice && currentSlice !== lastLoadedSlice) {
        lastLoadedSlice = currentSlice;
        selectedResultId = null;
        imageErrors = new Set();
        loadClusterResults();
    }

    // Expose a method to refresh results (can be called from parent if needed)
    export function refreshResults(options = {}) {
        if (currentSlice) {
            const hasSelection =
                selectedResultId !== null && selectedResultId !== undefined;
            const shouldAutoSelect =
                options.autoSelectFirst !== undefined
                    ? options.autoSelectFirst
                    : !hasSelection && autoSelectOnLoad;
            loadClusterResults({ autoSelectFirst: shouldAutoSelect });
        }
    }

    export function clearSelection() {
        selectedResultId = null;
        currentClusterResult = null;
    }

    export function autoSelectFirst() {
        loadClusterResults({ autoSelectFirst: true });
    }

    export function getScrollContainer() {
        return scrollContainer;
    }

    // Drag and drop handlers
    let draggedIndex = null;
    let draggedElement = null;
    let dragGhost = null;
    let dragOffset = { x: 0, y: 0 };

    function handleDragStart(event, index) {
        draggedIndex = index;
        draggedElement = event.currentTarget;
        event.dataTransfer.effectAllowed = "move";
        // Some browsers require at least one plain-text data item
        event.dataTransfer.setData("text/plain", "cluster-card");
        
        // Find the card container (parent element that contains the drag button)
        // The card container has dragover handler and contains the drag button
        // Look for the closest element with role="button" (the card container)
        let cardElement = event.currentTarget.closest('[role="button"]');
        
        // If not found, look for element with border class (cards have border class)
        if (!cardElement) {
            cardElement = event.currentTarget.closest('.border');
        }
        
        // Final fallback: traverse up the DOM tree to find card-like element
        if (!cardElement) {
            let parent = event.currentTarget.parentElement;
            let level = 0;
            while (parent && level < 4) {
                if (parent.classList && parent.classList.contains('border')) {
                    cardElement = parent;
                    break;
                }
                parent = parent.parentElement;
                level++;
            }
        }
        
        // Last resort: use parent's parent (usually the card container)
        if (!cardElement) {
            cardElement = event.currentTarget.parentElement?.parentElement || event.currentTarget.parentElement;
        }
        
        // Hide the default browser drag image by creating a transparent image
        const emptyImg = document.createElement('img');
        emptyImg.src = 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7';
        emptyImg.style.width = '1px';
        emptyImg.style.height = '1px';
        emptyImg.style.opacity = '0';
        document.body.appendChild(emptyImg);
        event.dataTransfer.setDragImage(emptyImg, 0, 0);
        // Remove the temporary image after a short delay
        setTimeout(() => {
            if (emptyImg.parentNode) {
                emptyImg.parentNode.removeChild(emptyImg);
            }
        }, 0);
        
        // Create a clone element that follows the mouse
        const rect = cardElement.getBoundingClientRect();
        dragOffset.x = event.clientX - rect.left;
        dragOffset.y = event.clientY - rect.top;
        
        dragGhost = cardElement.cloneNode(true);
        dragGhost.style.position = "fixed";
        dragGhost.style.pointerEvents = "none";
        dragGhost.style.opacity = "0.8";
        dragGhost.style.zIndex = "10000";
        dragGhost.style.transform = "rotate(2deg)";
        dragGhost.style.boxShadow = "0 10px 25px rgba(0, 0, 0, 0.3)";
        dragGhost.style.width = `${rect.width}px`;
        // Set initial position - only update vertical position during drag
        dragGhost.style.left = `${rect.left}px`;
        dragGhost.style.top = `${event.clientY - dragOffset.y}px`;
        dragGhost.style.margin = "0"; // Reset margin to avoid positioning issues
        document.body.appendChild(dragGhost);
        
        // Make original card element semi-transparent
        cardElement.style.opacity = "0.5";
        draggedElement = cardElement; // Update to reference the card, not the button
        
        // Update ghost position - only vertical (Y axis) following
        // During HTML5 drag, mousemove may not fire, so dragover is more reliable
        const updateGhostPosition = (e) => {
            if (dragGhost && e.clientY !== undefined) {
                // Only update vertical position, keep horizontal position fixed
                dragGhost.style.top = `${e.clientY - dragOffset.y}px`;
            }
        };
        
        // Listen to dragover on document level - this fires continuously during drag
        const documentDragOver = (e) => {
            e.preventDefault(); // Important: prevent default to allow drop
            updateGhostPosition(e);
        };
        
        document.addEventListener("dragover", documentDragOver, { passive: false });
        // Store the handler so we can remove it later
        dragGhost._updateHandler = documentDragOver;
    }

    function handleDragEnd(event) {
        // Restore the original card element's opacity
        if (draggedElement) {
            draggedElement.style.opacity = "1";
        }
        
        // Remove the ghost element
        if (dragGhost) {
            if (dragGhost._updateHandler) {
                document.removeEventListener("dragover", dragGhost._updateHandler);
            }
            if (dragGhost.parentNode) {
                dragGhost.parentNode.removeChild(dragGhost);
            }
            dragGhost = null;
        }
        
        draggedIndex = null;
        draggedElement = null;
    }

    function handleDragOver(event) {
        event.preventDefault();
        event.dataTransfer.dropEffect = "move";
        // Position update is handled by document-level dragover listener
    }

    function handleDragEnter(event) {
        event.preventDefault();
        if (event.currentTarget !== draggedElement) {
            event.currentTarget.style.borderColor = "#3b82f6";
        }
    }

    function handleDragLeave(event) {
        event.currentTarget.style.borderColor = "";
    }

    function handleDrop(event, dropIndex) {
        event.preventDefault();
        event.currentTarget.style.borderColor = "";

        if (draggedIndex === null || draggedIndex === dropIndex) {
            return;
        }

        // 创建新的手动排序数组
        const currentOrder =
            internalManualOrder ||
            sortedClusterResults.map((r) => r.cluster_result_id);
        const newOrder = [...currentOrder];
        const [removed] = newOrder.splice(draggedIndex, 1);
        newOrder.splice(dropIndex, 0, removed);

        // 更新内部状态
        internalManualOrder = newOrder;

        // 通知父组件
        dispatch("orderChange", { order: newOrder });
    }

    async function handleDeleteResult(result, event) {
        event.stopPropagation(); // 防止触发选择事件

        if (
            !confirm(
                `Are you sure you want to delete cluster result "${result.cluster_result_id}"? This action cannot be undone.`,
            )
        ) {
            return;
        }

        try {
            const res = await fetch(
                `${baseApi}/cluster-result?slice_id=${currentSlice}&cluster_result_id=${result.cluster_result_id}`,
                {
                    method: "DELETE",
                },
            );

            if (!res.ok) {
                const error = await res.json();
                throw new Error(
                    error.detail || "Failed to delete cluster result",
                );
            }

            // 如果删除的是当前选中的结果，清除选择
            if (selectedResultId === result.cluster_result_id) {
                selectedResultId = null;
                currentClusterResult = null;
                dispatch("selectClusterResult", {
                    result: null,
                    cluster_result_id: null,
                    method: null,
                    n_clusters: null,
                    epoch: null,
                    autoSelected: false,
                });
            }

            // 从手动排序中移除已删除的结果
            if (internalManualOrder) {
                internalManualOrder = internalManualOrder.filter(
                    (id) => id !== result.cluster_result_id,
                );
                dispatch("orderChange", {
                    order:
                        internalManualOrder.length > 0
                            ? internalManualOrder
                            : null,
                });
            }

            // 刷新结果列表
            await loadClusterResults({ autoSelectFirst: false });
        } catch (error) {
            console.error("Error deleting cluster result:", error);
            alert(`Failed to delete cluster result: ${error.message}`);
        }
    }
</script>

<div class="flex h-full min-h-0 flex-col">
    <div class="flex-shrink-0 {verticalLayout ? 'px-3 pt-2 pb-2' : 'pb-1'}">
        <div class="flex items-center justify-between gap-2 mb-1.5">
            <div class="text-[11px] text-gray-600">
                {#if sortedClusterResults.length}
                    <span
                        >{sortedClusterResults.length}
                        {sortedClusterResults.length === 1
                            ? "result"
                            : "results"}</span
                    >
                {:else}
                    <span>No results yet</span>
                {/if}
            </div>
            <button
                class="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 bg-slate-50 px-2.5 py-0.5 text-[11px] font-medium text-slate-700 transition hover:bg-slate-100 focus:outline-none focus:ring-2 focus:ring-slate-300 {showModal
                    ? 'ring-2 ring-slate-400 border-slate-400'
                    : ''}"
                on:click={() => (showModal ? closeNewForm() : openNewForm())}
            >
                {#if showModal}
                    <Minus size={14} />
                {:else}
                    <Plus size={14} />
                {/if}
                <span>New</span>
            </button>
        </div>

        <!-- Inline New Clustering form (no modal) -->
        {#if showModal}
            <div
                class="mt-1.5 rounded-md border border-gray-200 bg-white p-2 shadow-sm"
                role="region"
                aria-labelledby="inline-new-clustering-heading"
            >
                <p id="inline-new-clustering-heading" class="sr-only"
                    >New Clustering</p
                >
                <div class="mb-2">
                    <label
                        for="cluster-method-inline"
                        class="block text-[11px] font-medium text-gray-600 mb-0.5"
                        >Method</label
                    >
                    <select
                        id="cluster-method-inline"
                        class="w-full border border-gray-300 rounded px-2 py-1 text-[11px] bg-white focus:outline-none focus:ring-1 focus:ring-stone-400"
                        bind:value={newMethod}
                    >
                        {#each clusteringMethods as method}
                            <option value={method}>{method}</option>
                        {/each}
                    </select>
                </div>
                <div class="flex flex-wrap items-end gap-2">
                    <div>
                        <label
                            for="n-clusters-inline"
                            class="block text-[11px] font-medium text-gray-600 mb-0.5"
                            >n_clusters</label
                        >
                        <div class="flex items-center rounded border border-gray-300 bg-white overflow-hidden">
                            <button
                                type="button"
                                class="h-6 w-6 flex items-center justify-center text-gray-500 hover:bg-gray-100 disabled:opacity-40 disabled:hover:bg-transparent text-[11px]"
                                on:click={() => {
                                    if (newNClusters > 3) newNClusters -= 1;
                                }}
                                disabled={newNClusters <= 3}
                                aria-label="Decrease n_clusters"
                            >
                                −
                            </button>
                            <input
                                id="n-clusters-inline"
                                type="number"
                                class="w-8 border-0 border-x border-gray-300 py-0.5 text-center text-[11px] [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none focus:ring-0 focus:outline-none"
                                bind:value={newNClusters}
                                min="3"
                                max="25"
                                step="1"
                            />
                            <button
                                type="button"
                                class="h-6 w-6 flex items-center justify-center text-gray-500 hover:bg-gray-100 disabled:opacity-40 disabled:hover:bg-transparent text-[11px]"
                                on:click={() => {
                                    if (newNClusters < 25) newNClusters += 1;
                                }}
                                disabled={newNClusters >= 25}
                                aria-label="Increase n_clusters"
                            >
                                +
                            </button>
                        </div>
                    </div>
                    <div>
                        <label
                            for="epoch-inline"
                            class="block text-[11px] font-medium text-gray-600 mb-0.5"
                            >Epoch</label
                        >
                        <input
                            id="epoch-inline"
                            type="number"
                            class="w-14 border border-gray-300 rounded px-2 py-1 text-[11px] [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none focus:outline-none focus:ring-1 focus:ring-stone-400"
                            bind:value={newEpoch}
                            min="1"
                            step="1"
                        />
                    </div>
                    <div class="flex gap-1.5 ml-auto">
                        <button
                            type="button"
                            class="rounded border border-gray-300 bg-white px-2 py-1 text-[11px] font-medium text-gray-600 hover:bg-gray-50 focus:outline-none focus:ring-1 focus:ring-stone-400"
                            on:click={closeNewForm}
                        >
                            Cancel
                        </button>
                        <button
                            type="button"
                            class="rounded bg-stone-700 text-white px-2 py-1 text-[11px] font-medium hover:bg-stone-800 focus:outline-none focus:ring-1 focus:ring-stone-500"
                            on:click={handleRunCluster}
                        >
                            Run
                        </button>
                    </div>
                </div>
            </div>
        {/if}
    </div>

    <div class="flex-1 min-h-0 overflow-hidden">
        {#if loadingResults}
            <div
                class="flex justify-center items-center h-full text-sm text-gray-500"
            >
                Loading...
            </div>
        {:else if sortedClusterResults.length === 0}
            <div
                class="flex justify-center items-center h-full text-sm text-gray-500 border border-dashed border-gray-300 rounded"
            >
                No cluster results yet
            </div>
        {:else}
            <div class="h-full {verticalLayout ? 'px-3' : 'pt-1.5 pl-2 pr-2'}">
                <div
                    class="flex {verticalLayout
                        ? 'flex-col gap-2.5 overflow-y-auto overflow-x-hidden pt-1 pb-1'
                        : 'flex-nowrap gap-2.5 overflow-x-auto'} h-full {verticalLayout
                        ? ''
                        : 'pb-1 pt-0.5'} w-full"
                    bind:this={scrollContainer}
                    style="scrollbar-width: thin;"
                >
                    {#each sortedClusterResults as result, index (result.cluster_result_id)}
                        {#if verticalLayout}
                            <!-- Sidebar layout: horizontal card, image on the left, info + actions on the right -->
                            <div
                                class={`group relative flex flex-row items-stretch gap-2 border border-gray-200 rounded-lg bg-white/95 shadow-sm w-full flex-shrink-0 transition-all cursor-default ${
                                    selectedResultId === result.cluster_result_id
                                        ? "ring-2 ring-slate-400 border-slate-300 shadow"
                                        : "hover:shadow-md hover:-translate-y-px"
                                }`}
                                role="button"
                                tabindex="0"
                                on:click={() =>
                                    handleSelectResult(result, {
                                        autoSelected: false,
                                        isPreview: true,
                                    })}
                                on:keydown={(e) => {
                                    if (e.key === "Enter" || e.key === " ") {
                                        e.preventDefault();
                                        handleSelectResult(result, {
                                            autoSelected: false,
                                            isPreview: true,
                                        });
                                    }
                                }}
                                on:dragover={handleDragOver}
                                on:dragenter={handleDragEnter}
                                on:dragleave={handleDragLeave}
                                on:drop={(e) => handleDrop(e, index)}
                            >
                                <!-- Thumbnail -->
                                <div
                                    class="relative bg-white overflow-hidden flex-shrink-0 w-24 h-20 md:w-28 md:h-24 border-r border-gray-100"
                                >
                                    {#if result.plot_url && !imageErrors.has(result.cluster_result_id)}
                                        <img
                                            src="{baseApi}{result.plot_url}"
                                            alt={result.result_name}
                                            class="w-full h-full object-contain block transition-transform duration-150 ease-out group-hover:scale-[1.02]"
                                            loading="lazy"
                                            on:error={() => {
                                                imageErrors.add(
                                                    result.cluster_result_id,
                                                );
                                                imageErrors = new Set(
                                                    imageErrors,
                                                );
                                            }}
                                        />
                                    {:else}
                                        <div
                                            class="flex items-center justify-center w-full h-full"
                                        >
                                            <span
                                                class="text-[10px] text-gray-400"
                                                >No Preview</span
                                            >
                                        </div>
                                    {/if}
                                </div>

                                <!-- Info + actions -->
                                <div class="flex-1 flex flex-col px-2 py-1">
                                    <!-- Drag handle row (icon only) -->
                                    <div class="flex justify-end">
                                        <button
                                            type="button"
                                            class="flex items-center justify-center rounded px-1 text-gray-400 cursor-move hover:bg-slate-100"
                                            title="Drag to reorder"
                                            draggable="true"
                                            on:dragstart={(e) => handleDragStart(e, index)}
                                            on:dragend={handleDragEnd}
                                            on:mousedown|stopPropagation
                                            on:click|stopPropagation
                                        >
                                            <GripVertical size={12} />
                                        </button>
                                    </div>

                                    <!-- Info rows: ID / Method / Params as label + value -->
                                    <div class="mt-0.5 flex-1 min-w-0 text-left">
                                        <div class="space-y-0.5 text-[9px] text-gray-600">
                                            <div class="flex items-center gap-1 leading-none">
                                                <span
                                                    class="w-12 uppercase tracking-wide text-gray-500"
                                                    >ID</span
                                                >
                                                <span
                                                    class="flex-1 font-medium text-gray-700 truncate"
                                                    >{result.cluster_result_id}</span
                                                >
                                            </div>
                                            <div class="flex items-center gap-1 leading-none">
                                                <span
                                                    class="w-12 uppercase tracking-wide text-gray-500"
                                                    >Method</span
                                                >
                                                <span
                                                    class="flex-1 font-medium text-gray-800 truncate"
                                                    >{result.method}</span
                                                >
                                            </div>
                                            <div class="flex items-center gap-1 leading-none">
                                                <span
                                                    class="w-12 uppercase tracking-wide text-gray-500"
                                                    >Params</span
                                                >
                                                <span
                                                    class="flex-1 font-medium text-gray-800 truncate"
                                                >
                                                    k={result.n_clusters ?? "-"}{#if result.epoch}
                                                        · epoch={result.epoch}
                                                    {/if}
                                                </span>
                                            </div>
                                        </div>
                                    </div>

                                    <!-- Bottom row: date + action buttons -->
                                    <div class="mt-1 flex items-center justify-between gap-1">
                                        <div class="flex items-center gap-1 text-[9px] text-gray-400 leading-tight">
                                            {#if result.updated_at}
                                                <span>
                                                    {new Date(
                                                        result.updated_at,
                                                    ).toLocaleDateString()}
                                                </span>
                                            {/if}
                                        </div>
                                        <div class="flex items-center gap-1 flex-shrink-0">
                                            <!-- Open details button -->
                                            <button
                                                type="button"
                                                class="px-2 py-0.5 rounded border border-slate-300 bg-white text-[9px] text-slate-700 hover:bg-slate-50 transition-colors"
                                                title="Open details"
                                                on:click={(e) => {
                                                    e.stopPropagation();
                                                    handleSelectResult(result, {
                                                        autoSelected: false,
                                                        isPreview: false,
                                                    });
                                                }}
                                            >
                                                View
                                            </button>
                                            <button
                                                type="button"
                                                class="p-1 rounded hover:bg-slate-100 text-gray-400 hover:text-slate-600 transition-colors flex-shrink-0"
                                                on:click={(e) => {
                                                    e.stopPropagation();
                                                    const isHidden =
                                                        hiddenClusterResultIds.has(
                                                            result.cluster_result_id,
                                                        );
                                                    dispatch("toggleVisibility", {
                                                        clusterResultId:
                                                            result.cluster_result_id,
                                                        isHidden: !isHidden,
                                                    });
                                                }}
                                                title={hiddenClusterResultIds.has(
                                                    result.cluster_result_id,
                                                )
                                                    ? "Show in comparison"
                                                    : "Hide from comparison"}
                                            >
                                                {#if hiddenClusterResultIds.has(result.cluster_result_id)}
                                                    <EyeOff size={12} />
                                                {:else}
                                                    <Eye size={12} />
                                                {/if}
                                            </button>
                                            <button
                                                type="button"
                                                class="p-1 rounded hover:bg-red-50 text-gray-400 hover:text-red-600 transition-colors flex-shrink-0"
                                                on:click={(e) =>
                                                    handleDeleteResult(result, e)}
                                                title="Delete cluster result"
                                            >
                                                <Trash2 size={12} />
                                            </button>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        {:else}
                            <!-- Horizontal scroller layout (currently unused) keeps original vertical card design -->
                            <div
                                class={`group relative flex flex-col border border-gray-200 rounded-lg bg-white/95 shadow-sm w-[160px] flex-shrink-0 transition-all first:ml-1 cursor-default ${
                                    selectedResultId === result.cluster_result_id
                                        ? "ring-2 ring-slate-400 border-slate-300 shadow"
                                        : "hover:shadow-md hover:-translate-y-px"
                                }`}
                                role="button"
                                tabindex="0"
                                on:click={() =>
                                    handleSelectResult(result, {
                                        autoSelected: false,
                                        isPreview: true,
                                    })}
                                on:keydown={(e) => {
                                    if (e.key === "Enter" || e.key === " ") {
                                        e.preventDefault();
                                        handleSelectResult(result, {
                                            autoSelected: false,
                                            isPreview: true,
                                        });
                                    }
                                }}
                                on:dragover={handleDragOver}
                                on:dragenter={handleDragEnter}
                                on:dragleave={handleDragLeave}
                                on:drop={(e) => handleDrop(e, index)}
                            >
                                <div
                                    class="flex items-center justify-between gap-2 px-2 py-1 bg-gradient-to-r from-slate-50 via-white to-slate-50 border-b border-gray-100"
                                >
                                    <button
                                        type="button"
                                        class="flex items-center gap-1.5 flex-shrink-0 text-gray-400 cursor-move rounded px-1 hover:bg-slate-100"
                                        title="Drag to reorder"
                                        draggable="true"
                                        on:dragstart={(e) => handleDragStart(e, index)}
                                        on:dragend={handleDragEnd}
                                        on:mousedown|stopPropagation
                                        on:click|stopPropagation
                                    >
                                        <GripVertical size={12} />
                                    </button>
                                    <div
                                        class="flex flex-col gap-0.5 flex-1 min-w-0 text-left"
                                    >
                                        <div class="flex items-center">
                                            <span
                                                class="inline-block rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5 text-[9px] font-semibold uppercase tracking-wide text-slate-600 whitespace-nowrap"
                                                style="line-height: 1.2;"
                                            >
                                                {result.method}
                                            </span>
                                        </div>
                                        <div
                                            class="text-[9px] text-gray-500 leading-tight"
                                        >
                                            <span class="font-medium text-gray-600"
                                                >k={result.n_clusters ?? "-"}</span
                                            >
                                            {#if result.epoch}
                                                <span class="ml-1 text-gray-500"
                                                    >· epoch={result.epoch}</span
                                                >
                                            {/if}
                                        </div>
                                    </div>
                                    <div
                                        class="flex items-center gap-1 flex-shrink-0"
                                    >
                                        <div
                                            class="text-[9px] text-gray-400 text-right leading-tight"
                                        >
                                            {#if result.updated_at}
                                                <div>
                                                    {new Date(
                                                        result.updated_at,
                                                    ).toLocaleDateString()}
                                                </div>
                                            {/if}
                                        </div>
                                        <button
                                            type="button"
                                            class="p-1 rounded hover:bg-slate-100 text-gray-400 hover:text-slate-600 transition-colors flex-shrink-0"
                                            on:click={(e) => {
                                                e.stopPropagation();
                                                const isHidden =
                                                    hiddenClusterResultIds.has(
                                                        result.cluster_result_id,
                                                    );
                                                dispatch("toggleVisibility", {
                                                    clusterResultId:
                                                        result.cluster_result_id,
                                                    isHidden: !isHidden,
                                                });
                                            }}
                                            title={hiddenClusterResultIds.has(
                                                result.cluster_result_id,
                                            )
                                                ? "Show in comparison"
                                                : "Hide from comparison"}
                                        >
                                            {#if hiddenClusterResultIds.has(result.cluster_result_id)}
                                                <EyeOff size={12} />
                                            {:else}
                                                <Eye size={12} />
                                            {/if}
                                        </button>
                                        <button
                                            type="button"
                                            class="p-1 rounded hover:bg-red-50 text-gray-400 hover:text-red-600 transition-colors flex-shrink-0"
                                            on:click={(e) =>
                                                handleDeleteResult(result, e)}
                                            title="Delete cluster result"
                                        >
                                            <Trash2 size={12} />
                                        </button>
                                    </div>
                                </div>
                                <div
                                    class="flex-1 flex flex-col cursor-pointer"
                                    role="button"
                                    tabindex="0"
                                    on:click={() => handleSelectResult(result)}
                                    on:keydown={(e) => {
                                        if (e.key === "Enter" || e.key === " ") {
                                            e.preventDefault();
                                            handleSelectResult(result);
                                        }
                                    }}
                                >
                                    <div class="relative bg-white overflow-hidden">
                                        {#if result.plot_url && !imageErrors.has(result.cluster_result_id)}
                                            <img
                                                src="{baseApi}{result.plot_url}"
                                                alt={result.result_name}
                                                class="w-full h-24 md:h-28 lg:h-32 object-contain block transition-transform duration-150 ease-out group-hover:scale-[1.02]"
                                                loading="lazy"
                                                on:error={() => {
                                                    imageErrors.add(
                                                        result.cluster_result_id,
                                                    );
                                                    imageErrors = new Set(
                                                        imageErrors,
                                                    );
                                                }}
                                            />
                                        {:else}
                                            <div
                                                class="flex items-center justify-center py-4"
                                            >
                                                <span
                                                    class="text-[10px] text-gray-400"
                                                    >No Preview</span
                                                >
                                            </div>
                                        {/if}
                                    </div>

                                    <div
                                        class="px-2 py-1 text-[10px] text-gray-600 space-y-0.5"
                                    >
                                        <div
                                            class="flex items-center justify-between leading-none text-gray-500"
                                        >
                                            <span
                                                class="text-[9px] uppercase tracking-wide"
                                                >ID</span
                                            >
                                            <span
                                                class="text-[9px] font-medium text-gray-600"
                                                >{result.cluster_result_id}</span
                                            >
                                        </div>
                                    </div>
                                </div>
                            </div>
                        {/if}
                    {/each}
                </div>
            </div>
        {/if}
    </div>
</div>
