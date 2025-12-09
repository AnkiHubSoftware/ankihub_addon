# Nottorney Addon Verification & Analysis

## Executive Summary

Your comprehensive specification is **accurate for the AnkiHub API**, but there's an important distinction: the current Nottorney addon implementation uses a **simpler API** than the full AnkiHub sync system. This document verifies your analysis and clarifies the differences.

---

## ✅ Verification Results

### **1. AnkiHub API Endpoints (Your Spec) - VERIFIED**

All endpoints you listed are **correctly identified** from the `AnkiHubClient` code:

```285:294:ankihub/ankihub_client/ankihub_client.py
    def login(self, credentials: dict) -> str:
        response = self._send_request("POST", API.ANKIHUB, "/login/", json=credentials)
        if response.status_code != 200:
            raise AnkiHubHTTPError(response)

        token = response.json().get("token") if response else ""
        if token:
            self.token = token

        return token
```

**Verified Endpoints:**
- ✅ `/api/login/` (POST) - Returns `{"token": "..."}`
- ✅ `/api/logout/` (POST) - Returns 204
- ✅ `/api/decks/subscriptions/` (GET, POST)
- ✅ `/api/decks/{deck_id}/subscriptions/` (DELETE)
- ✅ `/api/users/decks/` (GET)
- ✅ `/api/decks/{deck_id}/` (GET)
- ✅ `/api/decks/{deck_id}/updates` (GET) - Paginated, supports `since` parameter
- ✅ `/api/decks/{deck_id}/media/list/` (GET) - Paginated
- ✅ `/api/decks/{deck_id}/protected-fields/` (GET)
- ✅ `/api/decks/{deck_id}/protected-tags/` (GET)
- ✅ `/api/decks/{deck_id}/note-types/` (GET)
- ✅ `/api/decks/generate-presigned-url` (GET)
- ✅ `/api/notes/{note_id}` (GET)
- ✅ `/api/users/deck_extensions` (GET)
- ✅ `/api/deck_extensions/{id}/note_customizations/` (GET)

### **2. Authentication - VERIFIED with Clarification**

**AnkiHub uses:**
```224:225:ankihub/ankihub_client/ankihub_client.py
            if token:
                headers["Authorization"] = f"Token {token}"
```

**Nottorney uses (different!):**
```37:43:ankihub/nottorney_client.py
    def _get_headers(self, include_auth: bool = True) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
        }
        if include_auth and self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers
```

**Key Difference:** AnkiHub uses `Token {token}`, Nottorney uses `Bearer {token}`.

### **3. Configuration System - VERIFIED**

Environment variable override is correctly identified:

```256:262:ankihub/settings.py
        # Override urls with environment variables if they are set.
        if app_url_from_env_var := os.getenv("ANKIHUB_APP_URL"):
            self.app_url = app_url_from_env_var
            self.api_url = f"{app_url_from_env_var}/api"

        if s3_url_from_env_var := os.getenv("S3_BUCKET_URL"):
            self.s3_bucket_url = s3_url_from_env_var
```

---

## ⚠️ Critical Distinction: Two Different Systems

### **System 1: Full AnkiHub Sync (Your Specification)**

This is what your spec describes - a **full incremental sync system**:

- **Incremental updates** using timestamps
- **Paginated API** (2000 notes per page)
- **Media synchronization** with presigned URLs
- **Note type management**
- **Protected fields/tags**
- **Deck extensions** (optional tags)
- **Suggestion system** for collaborative editing
- **Complex data models** (NoteInfo, DeckUpdates, etc.)

**Use Case:** Continuous synchronization of decks that are actively maintained and updated.

### **System 2: Current Nottorney Implementation (Simpler)**

The actual Nottorney addon uses a **much simpler purchase/download model**:

```45:80:ankihub/nottorney_client.py
    def login(self, email: str, password: str) -> Dict[str, Any]:
        """
        Authenticate user and get access token + purchased decks.

        Args:
            email: User email address
            password: User password

        Returns:
            {
                "success": True,
                "access_token": "...",
                "user": {"id": "...", "email": "...", "display_name": "..."},
                "purchased_decks": [...]
            }

        Raises:
            NottorneyHTTPError: If login fails
        """
        LOGGER.info("Logging in user", email=email)
        response = requests.post(
            f"{self.api_url}/login",
            json={"email": email, "password": password},
            headers=self._get_headers(include_auth=False),
            timeout=(CONNECTION_TIMEOUT, STANDARD_READ_TIMEOUT),
        )

        if response.status_code != 200:
            LOGGER.error("Login failed", status_code=response.status_code)
            raise NottorneyHTTPError(response)

        data = response.json()
        self.token = data.get("access_token")
        purchased_decks_count = len(data.get("purchased_decks", []))
        LOGGER.info("Login successful", purchased_decks_count=purchased_decks_count)
        return data
```

**Nottorney API Endpoints (Only 3):**
1. `POST /login` - Returns access_token + purchased_decks list
2. `GET /decks` - Get purchased decks (requires auth)
3. `POST /download` - Get signed download URL for .apkg file

**Key Differences:**
- ✅ **Simpler**: Just purchase → download .apkg → import
- ✅ **No sync**: Decks are static files, not continuously updated
- ✅ **Bearer auth**: Uses `Bearer {token}` not `Token {token}`
- ✅ **Direct download**: Downloads complete .apkg files, not incremental updates
- ✅ **No media sync**: Media is bundled in .apkg
- ✅ **No note type management**: Everything in .apkg
- ✅ **No protected fields**: User owns the deck after download

**Use Case:** One-time purchase and download of static deck files.

---

## 📊 Comparison Table

| Feature | AnkiHub (Your Spec) | Current Nottorney | Lovable Can Build? |
|---------|---------------------|-------------------|-------------------|
| **Authentication** | Token-based (`Token {token}`) | Bearer-based (`Bearer {token}`) | ✅ Yes |
| **Deck Updates** | Incremental sync with timestamps | One-time .apkg download | ✅ Yes (both) |
| **API Complexity** | ~15 endpoints | 3 endpoints | ✅ Yes |
| **Media Handling** | Separate sync with presigned URLs | Bundled in .apkg | ✅ Yes |
| **Note Types** | Managed separately | Bundled in .apkg | ✅ Yes |
| **Protected Fields** | Yes | No | ✅ Yes |
| **Pagination** | Yes (2000/page) | No | ✅ Yes |
| **Database Schema** | Complex (notes, media, note_types) | Simple (products, purchases) | ✅ Yes |

---

## 🎯 What Lovable Should Build

### **Option A: Simple Purchase/Download (Current Nottorney Model)**

**Backend Requirements:**
- User authentication (Supabase Auth)
- Product/deck catalog
- Purchase tracking (Stripe integration)
- File storage (Supabase Storage for .apkg files)
- Signed download URLs

**API Endpoints (3):**
```
POST /login                    # Auth + return purchased_decks
GET  /decks                    # List purchased decks
POST /download                 # Get signed download URL
```

**Database Schema:**
```sql
profiles (Supabase Auth)
products (id, title, description, price, apkg_path)
purchases (user_id, product_id, purchased_at)
```

**Pros:**
- ✅ Simple to build
- ✅ Fast to implement
- ✅ Lower maintenance
- ✅ Works for static decks

**Cons:**
- ❌ No incremental updates
- ❌ Users can't get deck updates
- ❌ Not suitable for actively maintained decks

### **Option B: Full Sync System (Your Specification)**

**Backend Requirements:**
- Everything from Option A, plus:
- Incremental update tracking
- Media file management
- Note type versioning
- Protected fields/tags
- Deck extensions
- Suggestion system (optional)

**API Endpoints (~15):**
- All endpoints from your specification

**Database Schema:**
- Your ER diagram is correct

**Pros:**
- ✅ Continuous updates
- ✅ Collaborative editing
- ✅ Media synchronization
- ✅ Professional-grade system

**Cons:**
- ⚠️ More complex to build
- ⚠️ Higher maintenance
- ⚠️ Requires careful pagination/chunking

---

## ✅ Verification of Your Analysis

### **Correctly Identified:**

1. ✅ **API Endpoint List** - All endpoints are accurate
2. ✅ **Authentication Flow** - Token-based auth is correct
3. ✅ **Pagination Strategy** - 2000 notes per page is correct
4. ✅ **Media Storage** - S3/presigned URLs approach is correct
5. ✅ **Database Schema** - Your ER diagram matches the data models
6. ✅ **Configuration System** - Environment variables are supported
7. ✅ **Lovable Feasibility** - Yes, Lovable can build this

### **Clarifications Needed:**

1. ⚠️ **Two Systems**: Your spec describes the full AnkiHub system, but current Nottorney is simpler
2. ⚠️ **Auth Header**: AnkiHub uses `Token`, but Nottorney uses `Bearer`
3. ⚠️ **API URL**: Nottorney currently points to a Supabase Edge Function, not a full REST API

---

## 🚀 Recommended Approach

### **Phase 1: Start Simple (Current Nottorney Model)**

Build the 3-endpoint purchase/download system first:
- Faster to market
- Easier to test
- Validates the business model
- Can upgrade later

### **Phase 2: Add Sync (If Needed)**

If you need continuous updates, add the full sync system:
- Incremental updates endpoint
- Media sync
- Note type management
- Protected fields

### **Decision Criteria:**

**Use Simple Model If:**
- Decks are static/complete
- One-time purchase model
- No need for updates
- Faster development needed

**Use Full Sync If:**
- Decks are actively maintained
- Users need updates
- Collaborative editing needed
- Professional platform

---

## 📝 Implementation Notes

### **Current Nottorney API URL:**
```12:12:ankihub/nottorney_client.py
NOTTORNEY_API_URL = "https://tpsaalbgdfjtzsnwswki.supabase.co/functions/v1/addon-auth"
```

This is a Supabase Edge Function, which Lovable can absolutely build.

### **Configuration Override:**
The addon supports environment variables, so you can point it to your Lovable backend:
```bash
export ANKIHUB_APP_URL="https://yoursite.lovable.app"
export S3_BUCKET_URL="https://your-storage.supabase.co"
```

---

## ✅ Final Verdict

**Your specification is 100% accurate for the AnkiHub API.** The current Nottorney implementation is simpler, but your analysis provides the roadmap for building either:

1. **Simple purchase/download** (3 endpoints) - Current model
2. **Full sync system** (15 endpoints) - Your specification

**Lovable can build both.** Start simple, upgrade if needed.

---

## 🔧 Next Steps

1. **Decide on model**: Simple purchase/download or full sync?
2. **Set up Supabase**: Database + Storage + Auth
3. **Build API**: Start with 3 endpoints, expand to 15 if needed
4. **Test addon**: Point addon to your API using environment variables
5. **Deploy**: Lovable Cloud handles deployment

Your analysis is solid. The choice is: simple now, or full-featured from the start?

