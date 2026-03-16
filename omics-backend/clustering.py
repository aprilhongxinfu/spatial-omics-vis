import os

from r_env import configure_r_home
configure_r_home()
import subprocess
print(subprocess.getoutput('echo $DYLD_FALLBACK_LIBRARY_PATH'))
import scanpy as sc
import pandas as pd
from sklearn import metrics
import torch
import warnings
warnings.filterwarnings("ignore")
import random
import matplotlib.pyplot as plt
import seaborn as sns
import SEDR

import ot
from pathlib import Path
import warnings
warnings.filterwarnings('ignore')
import SpaGCN as spg
from GraphST import GraphST
import numpy as np
# from meta_visualization import meta_viz
import matplotlib.pyplot as plt
import umap
from sklearn import manifold, datasets
from sklearn.decomposition import PCA
from sklearn.cluster import KMeans
import matplotlib.pyplot as plt
import numpy as np
from scipy.stats import gamma
from scipy import stats
from scipy.stats import rankdata
import cv2
random_seed = 2023
SEDR.fix_seed(random_seed)

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
def setup_seed(seed):
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    np.random.seed(seed)
    random.seed(seed)
    os.environ['PYTHONHASHSEED'] = str(seed)
    os.environ['CUBLAS_WORKSPACE_CONFIG'] = ':16:8'
    torch.manual_seed(seed)
    torch.cuda.manual_seed(seed)
    torch.backends.cudnn.enabled = False
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True

setup_seed(2023)


'''
Starter code for Python implementation of meta-visualization

Eric Sun (2022)
'''

from sklearn.metrics import pairwise_distances
from sklearn.neighbors import NearestNeighbors
from scipy.linalg import eigh
from sklearn.manifold import TSNE, MDS
import numpy as np

    
def meta_viz(visualizations, projection_method=None):
    '''
    Inputs:
       visualizations [list of numpy arrays]
        - list of numpy arrays of size n x 2 (columns being the two-dimensional coordinates of visualization)
        - each row should be matched to the same observation/sample across all elements in the list
       projection_method [str or None]
        - options are "tSNE" or "MDS" for reprojecting the meta-distance matrix into a meta-visualization
    
    Approach:
        1. Computes euclidean distance matrices from 2D embeddings
        2. Ensembles the euclidean distance according to eigenvector with largest eigenvalue
        3. Uses projection_method ["MDS", "tSNE"] to transform euclidean distance matrix to 2D embeddings
        
    Returns:
        meta_visualization [numpy array] - size nx2 two-dimensional visualization genereated using the meta-visualization approach
            - returned only if projection_method is not None
        meta_distances [numpy array] - size nxn pairwise euclidean distance matrix generated using meta-visualization approach
    '''
    # define number of samples
    n = visualizations[0].shape[0]
    K = len(visualizations)
    
    # Iterate and record distance matrices
    X_distance_matrix_list = []
    
    # compute pairwise distance matrix
    for X_embedded in visualizations:
        # assert X_embedded.shape[0] == n, "All visualization arrays need to have the same number of rows"
        # assert X_embedded.shape[1] == 2, "All visualization arrays need to have two columns"
        X_distance = pairwise_distances(X_embedded)
        X_distance_matrix_list.append(X_distance)
             
    # Compute weights for meta-visualization
    meta_distances = np.zeros((n,n))
    weights = np.zeros((n,K))
    for j in range(n):
        # fill in comparison matrix
        comp_mat = np.zeros((K,K))
        for i in range(K):
            for k in range(K):
                comp_mat[i,k] = np.sum(X_distance_matrix_list[k][:,j]*X_distance_matrix_list[i][:,j])/np.sqrt(np.sum(X_distance_matrix_list[k][:,j]**2))/np.sqrt(np.sum(X_distance_matrix_list[i][:,j]**2))
        # Eigenscore
        w, v = np.linalg.eig(comp_mat)
        weights[j,:] = np.abs(v[:,0])
    
        # Ensembles distance matrices
        matrix_norms = []
        for i in range(K):
            matrix_norms.append(X_distance_matrix_list[i][:,j]/np.sqrt(np.sum(X_distance_matrix_list[i][:,j]**2)))
        
        temp = np.zeros(matrix_norms[0].shape)
        for i in range(K):
            temp += matrix_norms[i]*weights[j,i]
            
        meta_distances[:,j] = temp
    # print(weights)
    
    meta_distances = np.nan_to_num((meta_distances+meta_distances.T)/2)
    
    # Re-project on 2D embedding space
    if projection_method is None:
        return(meta_distances, weights)
    else:
        if projection_method == "tSNE":
            tsne = TSNE(n_components=2, metric="precomputed", verbose=0).fit(meta_distances)
            meta_visualization = tsne.embedding_
        elif projection_method == "MDS":
            mds = skm.MDS(n_components=2, verbose=0, dissimilarity="precomputed").fit(meta_distances)
            meta_visualization = mds.embedding_
    
        return(meta_visualization, meta_distances)
    

def zp_quantile(x, y):
    # 获取排序后的索引
    o_x = np.argsort(x, axis=None)
    o_y = np.argsort(y, axis=None)

    # 对 x 和 y 进行排序
    sorted_x = x.flatten()[o_x]
    sorted_y = y.flatten()[o_y]

    # 对排序后的 x 和 y 计算排名
    rank_x = rankdata(sorted_x, method='average')
    rank_y = rankdata(sorted_y, method='average')

    # 使用排名的平均值进行插值
    interpolated_y = np.interp(rank_y, rank_x, sorted_y)

    # 根据原始顺序返回插值后的 y
    return interpolated_y.reshape(y.shape)
    
def generate_gamma_matrix(template, d, value_matrix):

    all_fit = None
    for m in range(len(d)):
        this_fit = gamma.fit(d[m].flatten())  
        # print("this_fit:", this_fit)
        if m == 0:
            all_fit = this_fit
        else:
            all_fit = np.vstack((all_fit, this_fit))
    
    ave_fit = np.mean(all_fit, axis=0)
    # print("ave_fit:", ave_fit)
    d_template = np.random.gamma(size=(len(d[1]), len(d[1])), shape=ave_fit[0], scale=1/ave_fit[2])
    # print("d_template:",d_template)
    # print("value_matrix:",value_matrix)
    this_d2 = zp_quantile(d_template, value_matrix)

    return this_d2

def refine_label(adata, radius=50, key='label'):
    n_neigh = radius
    new_type = []
    old_type = adata.obs[key].values
    
    #calculate distance
    position = adata.obsm['spatial']
    distance = ot.dist(position, position, metric='euclidean')
           
    n_cell = distance.shape[0]
    
    for i in range(n_cell):
        vec  = distance[i, :]
        index = vec.argsort()
        neigh_type = []
        for j in range(1, n_neigh+1):
            neigh_type.append(old_type[index[j]])
        max_type = max(neigh_type, key=neigh_type.count)
        new_type.append(max_type)
        
    new_type = [str(i) for i in list(new_type)]    
    #adata.obs['label_refined'] = np.array(new_type)
    
    return new_type


def mclust_R(adata, num_cluster, modelNames='EEE', used_obsm='emb_pca', random_seed=2020):
    np.random.seed(random_seed)
    import rpy2.robjects as robjects
    robjects.r.library("mclust")
    import rpy2.robjects.numpy2ri
    rpy2.robjects.numpy2ri.activate()
    r_random_seed = robjects.r['set.seed']
    r_random_seed(random_seed)
    rmclust = robjects.r['Mclust']
    res = rmclust(rpy2.robjects.numpy2ri.numpy2rpy(adata.obsm[used_obsm]), num_cluster, modelNames)
    mclust_res = np.array(res[-2])
    adata.obs['mclust'] = mclust_res
    adata.obs['mclust'] = adata.obs['mclust'].astype('int')
    adata.obs['mclust'] = adata.obs['mclust'].astype('category')
    return adata, res


print(os.environ['R_HOME'])
import rpy2.robjects as robjects
r_version = robjects.r('R.version.string')
print("R version:", r_version[0])

section_id = "151673"
n_clusters = 7
file_fold ='/home/junning/projectnvme/spatial-omics-vis/version1/spatial-omics-vis/omics-backend/data/'+ str(section_id)
adata = sc.read_visium(file_fold, count_file= 'filtered_feature_bc_matrix.h5', load_images=True)
## read ground truth
Ann_df = pd.read_csv("/home/junning/projectnvme/spatial-omics-vis/version1/spatial-omics-vis/omics-backend/data/" + str(section_id) + "/" + str(section_id)+ "_truth.txt", sep='\t', header=None, index_col=0)
Ann_df.columns = ['Ground Truth']
adata.obs['ground_truth'] = Ann_df.loc[adata.obs_names, 'Ground Truth']
adata = adata[~pd.isnull(adata.obs['ground_truth'])]


# ############### SEDR
print("SEDR clustering...")
adata.layers['count'] = adata.X.toarray()
sc.pp.filter_genes(adata, min_cells=50)
sc.pp.filter_genes(adata, min_counts=10)
sc.pp.normalize_total(adata, target_sum=1e6)
sc.pp.highly_variable_genes(adata, flavor="seurat_v3", layer='count', n_top_genes=2000)
adata = adata[:, adata.var['highly_variable'] == True]
sc.pp.scale(adata)

adata_X = PCA(n_components=200, random_state=42).fit_transform(adata.X)
adata.obsm['X_pca'] = adata_X
graph_dict = SEDR.graph_construction(adata, 12)
print(graph_dict)
sedr_net = SEDR.Sedr(adata.obsm['X_pca'], graph_dict, mode='clustering', device=device)
using_dec = True
if using_dec:
    sedr_net.train_with_dec(N=1)
else:
    sedr_net.train_without_dec(N=1)
sedr_feat, _, _, _ = sedr_net.process()
adata.obsm['SEDR'] = sedr_feat
SEDR.mclust_R(adata, n_clusters, use_rep='SEDR', key_added='SEDR')
ARI = metrics.adjusted_rand_score(adata.obs['ground_truth'], adata.obs['SEDR'])
print("ARI of SEDR:",ARI)

################# GraphST##################

n_clusters = 7
section_id = "151673"
file_fold ='/home/junning/projectnvme/spatial-omics-vis/version1/spatial-omics-vis/omics-backend/data/'+ str(section_id)
adata1 = sc.read_visium(file_fold, count_file= 'filtered_feature_bc_matrix.h5', load_images=True)
## read ground truth
Ann_df = pd.read_csv("/home/junning/projectnvme/spatial-omics-vis/version1/spatial-omics-vis/omics-backend/data/" + str(section_id) + "/" + str(section_id)+ "_truth.txt", sep='\t', header=None, index_col=0)
Ann_df.columns = ['Ground Truth']
adata1.obs['ground_truth'] = Ann_df.loc[adata1.obs_names, 'Ground Truth']

model = GraphST.GraphST(adata1, device = device,epochs = 600)
# train model
adata1 = model.train()
# set radius to specify the number of neighbors considered during refinement
radius = 50
tool = 'mclust' # mclust, leiden, and louvain

# clustering
from GraphST.utils import clustering

if tool == 'mclust':
   clustering(adata1, n_clusters, radius=radius, method=tool, refinement=True) # For DLPFC dataset, we use optional refinement step.
elif tool in ['leiden', 'louvain']:
   clustering(adata1, n_clusters, radius=radius, method=tool, start=0.1, end=2.0, increment=0.01, refinement=True)
adata1 = adata1[~pd.isnull(adata1.obs['ground_truth'])]
# calculate metric ARI
ARI = metrics.adjusted_rand_score(adata1.obs['domain'], adata1.obs['ground_truth'])
print("ARI of GraphST:", ARI)



####################### SpaGCN
adata2 = sc.read_visium(file_fold, count_file= 'filtered_feature_bc_matrix.h5', load_images=True)
spatial=pd.read_csv("/home/junning/projectnvme/spatial-omics-vis/version1/spatial-omics-vis/omics-backend/data/151673/spatial/tissue_positions_list.csv", header=None,index_col=0) 
adata2.obs["x1"]=spatial[1]
adata2.obs["x2"]=spatial[2]
adata2.obs["x3"]=spatial[3]
adata2.obs["x4"]=spatial[4]
adata2.obs["x5"]=spatial[5]
adata2.obs["x_array"]=adata2.obs["x2"]
adata2.obs["y_array"]=adata2.obs["x3"]
adata2.obs["x_pixel"]=adata2.obs["x4"]
adata2.obs["y_pixel"]=adata2.obs["x5"]
#Select captured samples
adata2=adata2[adata2.obs["x1"]==1]
adata2.var_names=[i.upper() for i in list(adata2.var_names)]
adata2.var["genename"]=adata2.var.index.astype("str")

img=cv2.imread("/home/junning/projectnvme/spatial-omics-vis/version1/spatial-omics-vis/omics-backend/data/151673/spatial/full_image.tif")
x_array=adata2.obs["x_array"].tolist()
y_array=adata2.obs["y_array"].tolist()
x_pixel=adata2.obs["x_pixel"].tolist()
y_pixel=adata2.obs["y_pixel"].tolist()

img_new=img.copy()
s=1
b=49
adj=spg.calculate_adj_matrix(x=x_pixel,y=y_pixel, x_pixel=x_pixel, y_pixel=y_pixel, image=img, beta=b, alpha=s, histology=True)
spg.prefilter_genes(adata2,min_cells=3) # avoiding all genes are zeros
spg.prefilter_specialgenes(adata2)
#Normalize and take log for UMI
sc.pp.normalize_per_cell(adata2)
sc.pp.log1p(adata2)
p=0.5 
l=spg.search_l(p, adj, start=0.01, end=1000, tol=0.001, max_run=100)
n_clusters=7
r_seed=t_seed=n_seed=100
res=spg.search_res(adata2, adj, l, n_clusters, start=0.7, step=0.1, tol=5e-3, lr=0.05, max_epochs=20, r_seed=r_seed, t_seed=t_seed, n_seed=n_seed)

clf=spg.SpaGCN()
clf.set_l(l)
random.seed(r_seed)
torch.manual_seed(t_seed)
np.random.seed(n_seed)
result = clf.train(adata2,adj,init_spa=True,init="louvain",res=res, tol=5e-3, lr=0.05, max_epochs=900)
adata2.obsm["spagcn_embed"] = clf.embed

y_pred, prob=clf.predict()
adata2.obs["pred"]= y_pred
adata2.obs["pred"]=adata2.obs["pred"].astype('category')
#Do cluster refinement(optional)
#shape="hexagon" for Visium data, "square" for ST data.
adj_2d=spg.calculate_adj_matrix(x=x_array,y=y_array, histology=False)
refined_pred=spg.refine(sample_id=adata2.obs.index.tolist(), pred=adata2.obs["pred"].tolist(), dis=adj_2d, shape="hexagon")
adata2.obs["refined_pred"]=refined_pred
adata2.obs["refined_pred"]=adata2.obs["refined_pred"].astype('category')

Ann_df = pd.read_csv("/home/junning/projectnvme/spatial-omics-vis/version1/spatial-omics-vis/omics-backend/data/" + str(section_id) + "/" + str(section_id)+ "_truth.txt", sep='\t', header=None, index_col=0)
Ann_df.columns = ['Ground Truth']
adata2.obs['ground_truth'] = Ann_df.loc[adata2.obs_names, 'Ground Truth']
adata2 = adata2[~pd.isnull(adata2.obs['ground_truth'])]

ARI = metrics.adjusted_rand_score(adata2.obs['pred'], adata2.obs['ground_truth'])
print("ARI of SpaGCN:", ARI)


emb_SEDR = adata.obsm["SEDR"]
emb_Graphst = adata1.obsm["emb"]
emb_spagcn = adata2.obsm["spagcn_embed"]

np.save("/home/junning/projectnvme/spatial-omics-vis/version1/spatial-omics-vis/omics-backend/data/emb_SEDR.npy",emb_SEDR)
np.save("/home/junning/projectnvme/spatial-omics-vis/version1/spatial-omics-vis/omics-backend/data/emb_Graphst.npy",emb_Graphst)
np.save("/home/junning/projectnvme/spatial-omics-vis/version1/spatial-omics-vis/omics-backend/data/emb_spagcn.npy",emb_spagcn)


# emb_SEDR = np.load("emb_SEDR.npy",allow_pickle = True)
# emb_Graphst = np.load("emb_Graphst.npy",allow_pickle = True)
# emb_spagcn = np.load("emb_spagcn.npy",allow_pickle = True)
# emb_het = np.load("/home/junning/projectnvme/spatial-omics-vis/version1/spatial-omics-vis/omics-backend/data/emb_het.npy",allow_pickle = True)
meta_embedding, meta_distances = meta_viz([emb_SEDR, emb_Graphst,emb_spagcn])
adata.obsm["meta"] = meta_embedding


# n_components_list = [2,4,5,6,10,15,20,25,30,35,40,45,50,60]
# # n_components_list = [30]
# for n_components in n_components_list:
#     pca = PCA(n_components = n_components, random_state=42) 
#     embedding_att = pca.fit_transform(adata.obsm['meta'].copy())
#     adata.obsm['meta_pca'] = embedding_att
#     adata, m_res = mclust_R(adata, used_obsm='meta_pca', num_cluster=7)
#     obs_df = adata.obs.dropna()
#     ARI = metrics.adjusted_rand_score(adata.obs['mclust'], adata.obs['ground_truth'])
#     print("n_components:",n_components)
#     print("ARI of meta:",ARI)

n_components = 50
pca = PCA(n_components = n_components, random_state=42) 
embedding_att = pca.fit_transform(adata.obsm['meta'].copy())
adata.obsm['meta_pca'] = embedding_att
adata, m_res = mclust_R(adata, used_obsm='meta_pca', num_cluster=7)
obs_df = adata.obs.dropna()
ARI = metrics.adjusted_rand_score(adata.obs['mclust'], adata.obs['ground_truth'])
# print("n_components:",n_components)
print("ARI of meta before refine:",ARI)
sc.pl.spatial(adata,
                img_key="hires",
                color=["ground_truth", "mclust"],
                title=["Ground truth", "ARI=%.4f"%ARI],
                show=False, save = "meta"+str(n_components)+".png")


new_type = refine_label(adata, radius = 50, key='mclust')
adata.obs['meta_refine'] = new_type 
ARI = metrics.adjusted_rand_score(adata.obs['meta_refine'], adata.obs['ground_truth'])
print("ARI of meta after refine:",ARI)
sc.pl.spatial(adata,
                img_key="hires",
                color=["ground_truth", "meta_refine"],
                title=["Ground truth", "ARI=%.4f"%ARI],
                show=False, save = "meta_refine"+str(n_components)+".png")



# print("Kmeans clustering...")
# kmeans = KMeans(n_clusters=7, random_state=42)
# kmeans.fit(meta_distances)
# centroids = kmeans.cluster_centers_
# labels = kmeans.labels_
# adata.obs['kmeans'] = labels
# ARI = metrics.adjusted_rand_score(adata.obs['kmeans'], adata.obs['ground_truth'])
# print("K-means:",ARI)


# ###################### zp. quantile ############################
# emb_SEDR = np.load("emb_SEDR.npy",allow_pickle = True)
# emb_Graphst = np.load("emb_GraphST.npy",allow_pickle = True)
# meta_distances = np.load("meta_distances.npy",allow_pickle = True)

# # Set the distribution template and parameters
# template_type = "gamma"
# gamma_shape = 3
# gamma_rate = 3

# d = [emb_SEDR, emb_Graphst]
# # Generate template distribution
# # print("meta-distances before alignment:", meta_distances)
# # Apply quantile normalization
# generated_matrices = generate_gamma_matrix(template_type, d, meta_distances)
# # print("meta-distances after alignment:", generated_matrices)
# # generated_fit = gamma.fit(generated_matrices.flatten()) 
# # print("generated_fit:",generated_fit)
# adata.obsm["meta_align"] = generated_matrices


# n_components_list = [2,4,5,6,10,15,20,25,30,35,40,45,50,64]
# # n_components_list = [35]
# for n_components in n_components_list:
#     pca = PCA(n_components = n_components, random_state=42) 
#     embedding_att = pca.fit_transform(adata.obsm['meta_align'].copy())
#     adata.obsm['meta_pca'] = embedding_att
#     adata, m_res = mclust_R(adata, used_obsm='meta_pca', num_cluster=7)
#     obs_df = adata.obs.dropna()
#     ARI = metrics.adjusted_rand_score(adata.obs['mclust'], adata.obs['ground_truth'])
#     print("n_components:",n_components)
#     print("ARI of meta:",ARI)