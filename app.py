import streamlit as st
import pandas as pd
from datetime import datetime
from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException

# --- CONFIG ---
st.set_page_config(page_title="Grant Guardian | Agency Pro", page_icon="🛡️", layout="wide")

# --- SECRETS MANAGEMENT (AUTO-FILL) ---
def get_secret(key):
    # Checks if secrets exist in Streamlit Cloud config
    if "GOOGLE_ADS" in st.secrets and key in st.secrets["GOOGLE_ADS"]:
        return st.secrets["GOOGLE_ADS"][key]
    return ""

# --- STATE ---
if 'client_name' not in st.session_state: st.session_state['client_name'] = "Unassigned"
if 'data_cache' not in st.session_state: st.session_state['data_cache'] = {}

# --- HELPER: DEPLOYMENT ENGINE ---
def deploy_full_stack(client, customer_id, data):
    logs = []
    campaign_map = {} 
    ad_group_map = {}
    asset_map = {}

    camp_service = client.get_service("CampaignService")
    budget_service = client.get_service("CampaignBudgetService")
    ag_service = client.get_service("AdGroupService")
    kw_service = client.get_service("AdGroupCriterionService")
    ad_service = client.get_service("AdGroupAdService")
    asset_service = client.get_service("AssetService")
    camp_asset_service = client.get_service("CampaignAssetService")

    # 1. CAMPAIGNS & BUDGETS
    if 'structure' in data:
        st.write("🏗️ Building Campaigns...")
        df_c = data['structure']
        unique_camps = df_c[['Campaign', 'Campaign Daily Budget']].drop_duplicates()

        for _, row in unique_camps.iterrows():
            try:
                # Budget
                b_op = client.get_type("CampaignBudgetOperation")
                budget = b_op.create
                budget.name = f"{row['Campaign']} - {datetime.now().microsecond}"
                budget.amount_micros = int(row['Campaign Daily Budget'] * 1_000_000)
                budget.delivery_method = client.enums.BudgetDeliveryMethodEnum.STANDARD
                
                b_resp = budget_service.mutate_campaign_budgets(customer_id=customer_id, operations=[b_op])
                budget_id = b_resp.results[0].resource_name

                # Campaign
                c_op = client.get_type("CampaignOperation")
                camp = c_op.create
                camp.name = row['Campaign']
                camp.advertising_channel_type = client.enums.AdvertisingChannelTypeEnum.SEARCH
                camp.status = client.enums.CampaignStatusEnum.PAUSED
                camp.manual_cpc.enhanced_cpc_enabled = False
                camp.campaign_budget = budget_id
                camp.network_settings.target_google_search = True
                camp.network_settings.target_search_network = True
                camp.network_settings.target_content_network = False

                c_resp = camp_service.mutate_campaigns(customer_id=customer_id, operations=[c_op])
                resource_name = c_resp.results[0].resource_name
                campaign_map[row['Campaign']] = resource_name
                logs.append(f"✅ Campaign Created: {row['Campaign']}")
            except Exception as e:
                logs.append(f"❌ Campaign Error ({row['Campaign']}): {e}")

    # 2. AD GROUPS
    if 'structure' in data and campaign_map:
        st.write("📂 Building Ad Groups...")
        df_ag = data['structure'][['Campaign', 'Ad Group']].drop_duplicates()
        for _, row in df_ag.iterrows():
            if row['Campaign'] in campaign_map:
                try:
                    ag_op = client.get_type("AdGroupOperation")
                    ag = ag_op.create
                    ag.name = row['Ad Group']
                    ag.campaign = campaign_map[row['Campaign']]
                    ag.type_ = client.enums.AdGroupTypeEnum.SEARCH_STANDARD
                    ag.cpc_bid_micros = 2000000 
                    
                    ag_resp = ag_service.mutate_ad_groups(customer_id=customer_id, operations=[ag_op])
                    ag_id = ag_resp.results[0].resource_name
                    ad_group_map[f"{row['Campaign']}|{row['Ad Group']}"] = ag_id
                    logs.append(f"🔹 Ad Group Created: {row['Ad Group']}")
                except Exception as e:
                    logs.append(f"❌ Ad Group Error: {e}")

    # 3. KEYWORDS
    if 'keywords' in data and ad_group_map:
        st.write("🔑 Adding Keywords...")
        df_k = data['keywords']
        ops = []
        for _, row in df_k.iterrows():
            key = f"{row['Campaign']}|{row['Ad Group']}"
            if key in ad_group_map:
                op = client.get_type("AdGroupCriterionOperation")
                kw = op.create
                kw.ad_group = ad_group_map[key]
                kw.keyword.text = row['Keyword']
                mt = row['Match Type'].lower()
                if 'broad' in mt: kw.keyword.match_type = client.enums.KeywordMatchTypeEnum.BROAD
                elif 'phrase' in mt: kw.keyword.match_type = client.enums.KeywordMatchTypeEnum.PHRASE
                elif 'exact' in mt: kw.keyword.match_type = client.enums.KeywordMatchTypeEnum.EXACT
                ops.append(op)
        if ops:
            try:
                for i in range(0, len(ops), 500):
                    kw_service.mutate_ad_group_criteria(customer_id=customer_id, operations=ops[i:i+500])
                logs.append(f"✅ Added {len(ops)} Keywords.")
            except Exception as e:
                logs.append(f"❌ Keyword Error: {e}")

    # 4. ADS
    if 'ads' in data and ad_group_map:
        st.write("✍️ Writing Ads...")
        df_a = data['ads']
        ops = []
        for _, row in df_a.iterrows():
            key = f"{row['Campaign']}|{row['Ad Group']}"
            if key in ad_group_map:
                op = client.get_type("AdGroupAdOperation")
                ad_group_ad = op.create
                ad_group_ad.ad_group = ad_group_map[key]
                ad_group_ad.status = client.enums.AdGroupAdStatusEnum.ENABLED
                rsa = ad_group_ad.ad.responsive_search_ad
                for i in range(1, 16):
                    if f'Headline {i}' in row and pd.notna(row[f'Headline {i}']):
                        asset = client.get_type("AdTextAsset")
                        asset.text = str(row[f'Headline {i}'])
                        rsa.headlines.append(asset)
                for i in range(1, 5):
                    if f'Description {i}' in row and pd.notna(row[f'Description {i}']):
                        asset = client.get_type("AdTextAsset")
                        asset.text = str(row[f'Description {i}'])
                        rsa.descriptions.append(asset)
                ad_group_ad.ad.final_urls.append(row['Final URL'])
                ops.append(op)
        if ops:
            try:
                for i in range(0, len(ops), 500):
                    ad_service.mutate_ad_group_ads(customer_id=customer_id, operations=ops[i:i+500])
                logs.append(f"✅ Created {len(ops)} RSAs.")
            except Exception as e:
                logs.append(f"❌ Ad Creation Error: {e}")

    # 5. SITELINKS
    if 'sitelinks' in data and campaign_map:
        st.write("🔗 Linking Sitelinks...")
        df_sl = data['sitelinks']
        unique_assets = df_sl[['Asset ID', 'Link Text', 'Final URL']].drop_duplicates()
        for _, row in unique_assets.iterrows():
            try:
                op = client.get_type("AssetOperation")
                asset = op.create
                asset.sitelink_asset.link_text = str(row['Link Text'])
                asset.final_urls.append(row['Final URL'])
                resp = asset_service.mutate_assets(customer_id=customer_id, operations=[op])
                asset_map[row['Asset ID']] = resp.results[0].resource_name
            except Exception as e:
                logs.append(f"❌ Asset Error {row['Asset ID']}: {e}")
        
        link_ops = []
        for _, row in df_sl.iterrows():
            if row['Campaign'] in campaign_map and row['Asset ID'] in asset_map:
                op = client.get_type("CampaignAssetOperation")
                ca = op.create
                ca.campaign = campaign_map[row['Campaign']]
                ca.asset = asset_map[row['Asset ID']]
                ca.field_type = client.enums.AssetFieldTypeEnum.SITELINK
                link_ops.append(op)
        if link_ops:
            try:
                camp_asset_service.mutate_campaign_assets(customer_id=customer_id, operations=link_ops)
                logs.append(f"✅ Linked {len(link_ops)} Sitelinks.")
            except Exception as e:
                logs.append(f"❌ Sitelink Linking Error: {e}")

    return logs

# --- UI SETUP ---
with st.sidebar:
    st.title("🛡️ Grant Guardian")
    st.caption("Agency Pro Edition")
    
    st.divider()
    
    # --- CLIENT SELECTOR ---
    st.markdown("**1. Client Setup**")
    new_client = st.text_input("Active Client Name", value=st.session_state['client_name'])
    if new_client != st.session_state['client_name']:
        st.session_state['client_name'] = new_client
        st.session_state['data_cache'] = {} 
        st.rerun()
    
    st.divider()

    # --- API CREDENTIALS (Auto-filled) ---
    with st.expander("2. API Connections", expanded=True):
        dev_token = st.text_input("Developer Token", value=get_secret("developer_token"), type="password")
        client_id = st.text_input("Client ID", value=get_secret("client_id"), type="password")
        client_secret = st.text_input("Client Secret", value=get_secret("client_secret"), type="password")
        refresh_token = st.text_input("Refresh Token", value=get_secret("refresh_token"), type="password")
        login_id = st.text_input("Login Customer ID (MCC)", value=get_secret("login_customer_id"))

st.markdown(f"### 📂 Client Workspace: **{st.session_state['client_name']}**")

t1, t2, t3 = st.tabs(["1. Upload", "2. Validate", "3. EXECUTE"])

with t1:
    col1, col2 = st.columns(2)
    with col1:
        f1 = st.file_uploader("Structure (CSV)", key="f1")
        if f1: st.session_state['data_cache']['structure'] = pd.read_csv(f1)
        f2 = st.file_uploader("Keywords (CSV)", key="f2")
        if f2: st.session_state['data_cache']['keywords'] = pd.read_csv(f2)
        f3 = st.file_uploader("Ads (TSV)", key="f3")
        if f3: 
             try: st.session_state['data_cache']['ads'] = pd.read_csv(f3, sep='\\t')
             except: st.session_state['data_cache']['ads'] = pd.read_csv(f3)
    with col2:
        f4 = st.file_uploader("Sitelink Assets", key="f4")
        f5 = st.file_uploader("Sitelink Associations", key="f5")
        if f4 and f5:
            a = pd.read_csv(f4)
            b = pd.read_csv(f5)
            st.session_state['data_cache']['sitelinks'] = pd.merge(b, a, on="Asset ID", how="left")
            st.success(f"Linked {len(st.session_state['data_cache']['sitelinks'])} sitelinks")

with t3:
    st.header("🚀 Launch to Google Ads")
    cust_id = st.text_input("Target Client ID (10 digits)")
    
    if st.button("START DEPLOYMENT", type="primary"):
        if not (dev_token and cust_id):
            st.error("Missing Credentials")
        else:
            creds = {
                "developer_token": dev_token,
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "use_proto_plus": True
            }
            if login_id: creds["login_customer_id"] = login_id
            
            try:
                g_client = GoogleAdsClient.load_from_dict(creds)
                with st.spinner("Executing Full-Stack Deployment..."):
                    logs = deploy_full_stack(g_client, cust_id.replace("-",""), st.session_state['data_cache'])
                
                for l in logs:
                    if "❌" in l: st.error(l)
                    else: st.success(l)
                    
            except Exception as e:
                st.error(f"Critical Connection Error: {e}")
