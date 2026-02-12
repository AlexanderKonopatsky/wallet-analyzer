import { useState, useEffect } from 'react'
import './WalletSidebar.css'
import { apiCall } from '../utils/api'

const WALLET_ACTIONS = [
  { id: 'report', label: 'Report' },
  { id: 'profile', label: 'Profile' },
]

function WalletSidebar({ wallets, selectedWallet, onSelect, onAction, onSaveTag, onRefresh, onBulkRefresh }) {
  const [inputValue, setInputValue] = useState('')
  const [editingAddr, setEditingAddr] = useState(null)
  const [tagValue, setTagValue] = useState('')

  // Categories state
  const [categories, setCategories] = useState([])
  const [expandedCategories, setExpandedCategories] = useState({})
  const [showCategoryModal, setShowCategoryModal] = useState(false)
  const [categoryModalMode, setCategoryModalMode] = useState('create') // 'create' | 'edit'
  const [editingCategory, setEditingCategory] = useState(null)

  // Context menu state
  const [contextMenu, setContextMenu] = useState(null)

  // Drag and drop state
  const [draggingWallet, setDraggingWallet] = useState(null)

  const isValidAddress = (addr) => /^0x[a-fA-F0-9]{40}$/.test(addr)
  const shortAddr = (addr) => `${addr.slice(0, 6)}...${addr.slice(-4)}`

  // Load categories from API
  useEffect(() => {
    fetchCategories()
  }, [])

  const fetchCategories = async () => {
    try {
      const res = await apiCall('/api/categories')
      console.log('Categories API response status:', res.status)
      const data = await res.json()
      console.log('Categories data:', data)
      setCategories(data.categories || [])

      // Initialize expanded state from categories
      const expanded = {}
      data.categories.forEach(cat => {
        expanded[cat.id] = cat.expanded !== false
      })
      setExpandedCategories(expanded)
      console.log('Categories set:', data.categories?.length || 0)
    } catch (err) {
      console.error('Failed to load categories:', err)
    }
  }

  const toggleCategory = async (categoryId) => {
    const newExpanded = !expandedCategories[categoryId]
    setExpandedCategories(prev => ({ ...prev, [categoryId]: newExpanded }))

    // Save expanded state to backend
    try {
      await apiCall(`/api/categories/${categoryId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ expanded: newExpanded })
      })
    } catch (err) {
      console.error('Failed to save category state:', err)
    }
  }

  const handleAddCategory = () => {
    setCategoryModalMode('create')
    setEditingCategory({ name: '', color: '#3b82f6' })
    setShowCategoryModal(true)
  }

  const handleEditCategory = (category) => {
    setCategoryModalMode('edit')
    setEditingCategory({ ...category })
    setShowCategoryModal(true)
  }

  const handleSaveCategory = async () => {
    try {
      console.log('Saving category:', categoryModalMode, editingCategory)
      if (categoryModalMode === 'create') {
        const res = await apiCall('/api/categories', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            name: editingCategory.name,
            color: editingCategory.color
          })
        })
        console.log('Create category response:', res.status)
        if (res.ok) {
          const newCat = await res.json()
          console.log('Category created:', newCat)
          await fetchCategories()
          onRefresh?.()
        } else {
          const error = await res.text()
          console.error('Failed to create category:', error)
        }
      } else {
        const res = await apiCall(`/api/categories/${editingCategory.id}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            name: editingCategory.name,
            color: editingCategory.color
          })
        })
        console.log('Update category response:', res.status)
        if (res?.ok) {
          await fetchCategories()
          onRefresh?.()
        } else {
          const error = res ? await res.text() : 'Unauthorized'
          console.error('Failed to update category:', error)
        }
      }
      setShowCategoryModal(false)
    } catch (err) {
      console.error('Failed to save category:', err)
    }
  }

  const handleDeleteCategory = async (categoryId) => {
    if (!confirm('Delete category? Wallets will become uncategorized.')) return

    try {
      const res = await apiCall(`/api/categories/${categoryId}`, { method: 'DELETE' })
      if (res?.ok) {
        await fetchCategories()
        onRefresh?.()
      } else {
        const error = res ? await res.text() : 'Unauthorized'
        console.error('Failed to delete category:', error)
      }
    } catch (err) {
      console.error('Failed to delete category:', err)
    }
  }

  const handleMoveWalletToCategory = async (walletAddr, categoryId) => {
    try {
      const res = await apiCall(`/api/wallets/${walletAddr}/category`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ category_id: categoryId })
      })
      if (res.ok) {
        onRefresh?.()
      }
    } catch (err) {
      console.error('Failed to move wallet:', err)
    }
  }

  const handleAddWallet = () => {
    const addr = inputValue.trim().toLowerCase()
    if (isValidAddress(addr)) {
      onSelect(addr)
      setInputValue('')
    }
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Enter') handleAddWallet()
  }

  // Group wallets by category
  const walletsByCategory = {}
  const uncategorizedWallets = []

  wallets.forEach(w => {
    if (w.category) {
      const catId = w.category.id
      if (!walletsByCategory[catId]) {
        walletsByCategory[catId] = []
      }
      walletsByCategory[catId].push(w)
    } else {
      uncategorizedWallets.push(w)
    }
  })

  // Drag handlers
  const handleDragStart = (e, wallet) => {
    setDraggingWallet(wallet.address.toLowerCase())
    e.dataTransfer.effectAllowed = 'move'
  }

  const handleDragOver = (e) => {
    e.preventDefault()
    e.dataTransfer.dropEffect = 'move'
  }

  const handleDrop = (e, categoryId) => {
    e.preventDefault()
    e.stopPropagation()

    if (draggingWallet) {
      handleMoveWalletToCategory(draggingWallet, categoryId)
      setDraggingWallet(null)
    }
  }

  const handleDragEnd = () => {
    setDraggingWallet(null)
  }

  // Context menu handlers
  const openContextMenu = (x, y, type, item) => {
    setContextMenu({
      x,
      y,
      type,
      item
    })
  }

  const handleContextMenu = (e, type, item) => {
    e.preventDefault()
    e.stopPropagation()
    openContextMenu(e.clientX, e.clientY, type, item)
  }

  const handleCategoryActionsClick = (e, category) => {
    e.preventDefault()
    e.stopPropagation()
    const rect = e.currentTarget.getBoundingClientRect()
    openContextMenu(rect.left, rect.bottom + 6, 'category', category)
  }

  useEffect(() => {
    const closeContextMenu = () => setContextMenu(null)
    document.addEventListener('click', closeContextMenu)
    return () => document.removeEventListener('click', closeContextMenu)
  }, [])

  const handleHideWallet = async (walletAddr, e) => {
    e.stopPropagation()

    try {
      const res = await apiCall(`/api/wallets/${walletAddr}/hide`, {
        method: 'POST'
      })

      if (res.ok) {
        onRefresh?.()
      }
    } catch (err) {
      console.error('Failed to hide wallet:', err)
    }
  }

  const renderWalletCard = (w) => {
    const addr = w.address.toLowerCase()
    const isActive = addr === selectedWallet

    return (
      <div
        key={addr}
        className={`wallet-card${isActive ? ' active' : ''}`}
        draggable
        onDragStart={(e) => handleDragStart(e, w)}
        onDragEnd={handleDragEnd}
        onClick={() => onSelect(addr)}
        onContextMenu={(e) => handleContextMenu(e, 'wallet', w)}
      >
        {w.category && (
          <div
            className="wallet-card-category-indicator"
            style={{ backgroundColor: w.category.color }}
          />
        )}
        <button
          className="wallet-card-remove"
          onClick={(e) => handleHideWallet(addr, e)}
          title="Hide wallet"
        >
          ×
        </button>
        <div className="wallet-card-top">
          {editingAddr === addr ? (
            <div className="wallet-card-tag-edit" onClick={e => e.stopPropagation()}>
              <input
                type="text"
                className="wallet-card-tag-input"
                value={tagValue}
                onChange={e => setTagValue(e.target.value)}
                onKeyDown={e => {
                  if (e.key === 'Enter') {
                    onSaveTag?.(addr, tagValue.trim())
                    setEditingAddr(null)
                  }
                  if (e.key === 'Escape') setEditingAddr(null)
                }}
                placeholder="Wallet name..."
                autoFocus
                maxLength={50}
              />
              <button
                className="wallet-card-tag-save"
                onClick={() => {
                  onSaveTag?.(addr, tagValue.trim())
                  setEditingAddr(null)
                }}
              >✓</button>
            </div>
          ) : w.tag ? (
            <span
              className="wallet-card-tag"
              onClick={e => {
                e.stopPropagation()
                setTagValue(w.tag)
                setEditingAddr(addr)
              }}
            >{w.tag}</span>
          ) : (
            <span
              className="wallet-card-notag wallet-card-notag--clickable"
              onClick={e => {
                e.stopPropagation()
                setTagValue('')
                setEditingAddr(addr)
              }}
            >+ Add name</span>
          )}
          {w.has_new_data && <span className="wallet-card-badge" title="New data" />}
        </div>
        <div className="wallet-card-bottom">
          <span className="wallet-card-address">{shortAddr(w.address)}</span>
          <span className="wallet-card-txcount">{w.tx_count} txs</span>
        </div>
        <div className="wallet-card-actions">
          {WALLET_ACTIONS.map(action => (
            <button
              key={action.id}
              className="wallet-action-btn"
              onClick={(e) => {
                e.stopPropagation()
                onAction?.(addr, action.id)
              }}
            >
              {action.label}
            </button>
          ))}
        </div>
      </div>
    )
  }

  return (
    <div className="wallet-sidebar">
      <div className="sidebar-header">
        <div className="sidebar-add">
          <input
            type="text"
            className="sidebar-input"
            placeholder="0x address..."
            value={inputValue}
            onChange={e => setInputValue(e.target.value)}
            onKeyDown={handleKeyDown}
            spellCheck={false}
          />
          <button
            className="sidebar-add-btn"
            onClick={handleAddWallet}
            disabled={!isValidAddress(inputValue.trim())}
          >
            Load
          </button>
        </div>
        <button className="category-add-btn" onClick={handleAddCategory}>
          ➕ Add Category
        </button>
        <button
          className="bulk-refresh-btn"
          onClick={() => onBulkRefresh?.('all')}
          title="Update all wallets"
        >
          🔄 Update All
        </button>
      </div>

      <div className="wallet-list">
        {wallets.length === 0 && (
          <div className="wallet-list-empty">
            No tracked wallets.<br />
            Paste address above.
          </div>
        )}

        {/* Categories */}
        {categories.map(cat => {
          const categoryWallets = walletsByCategory[cat.id] || []
          const isExpanded = expandedCategories[cat.id] !== false

          return (
            <div key={cat.id} className="category-section">
              <div
                className="category-header"
                onDrop={(e) => handleDrop(e, cat.id)}
                onDragOver={handleDragOver}
                onContextMenu={(e) => handleContextMenu(e, 'category', cat)}
              >
                <div
                  className="category-header-content"
                  onClick={() => toggleCategory(cat.id)}
                >
                  <div className="category-color" style={{ backgroundColor: cat.color }} />
                  <span className="category-icon">{isExpanded ? '▼' : '▶'}</span>
                  <span className="category-name">{cat.name}</span>
                  <span className="category-count">({categoryWallets.length})</span>
                </div>
                <div className="category-header-actions">
                  <button
                    className="category-manage-btn"
                    onClick={(e) => handleCategoryActionsClick(e, cat)}
                    title="Edit or delete category"
                  >
                    Manage
                  </button>
                  <button
                    className="category-refresh-btn"
                    onClick={(e) => {
                      e.stopPropagation()
                      onBulkRefresh?.(cat.id)
                    }}
                    title="Update all wallets in category"
                  >
                    🔄
                  </button>
                </div>
              </div>

              {isExpanded && (
                <div className="category-wallets">
                  {categoryWallets.map(w => renderWalletCard(w))}
                </div>
              )}
            </div>
          )
        })}

        {/* Uncategorized wallets */}
        {uncategorizedWallets.length > 0 && (
          <div className="category-section">
            <div
              className="category-header category-header-uncategorized"
              onDrop={(e) => handleDrop(e, null)}
              onDragOver={handleDragOver}
            >
              <div className="category-header-content">
                <span className="category-name">Uncategorized</span>
                <span className="category-count">({uncategorizedWallets.length})</span>
              </div>
            </div>
            <div className="category-wallets">
              {uncategorizedWallets.map(w => renderWalletCard(w))}
            </div>
          </div>
        )}
      </div>

      {/* Category Modal */}
      {showCategoryModal && (
        <div className="modal-overlay" onClick={() => setShowCategoryModal(false)}>
          <div className="modal-content" onClick={e => e.stopPropagation()}>
            <h3>{categoryModalMode === 'create' ? 'Create Category' : 'Edit Category'}</h3>
            <div className="modal-field">
              <label>Name:</label>
              <input
                type="text"
                value={editingCategory.name}
                onChange={e => setEditingCategory({ ...editingCategory, name: e.target.value })}
                placeholder="Category name"
                autoFocus
              />
            </div>
            <div className="modal-field">
              <label>Color:</label>
              <input
                type="color"
                value={editingCategory.color}
                onChange={e => setEditingCategory({ ...editingCategory, color: e.target.value })}
              />
            </div>
            <div className="modal-actions">
              <button onClick={handleSaveCategory} disabled={!editingCategory.name.trim()}>
                {categoryModalMode === 'create' ? 'Create' : 'Save'}
              </button>
              <button onClick={() => setShowCategoryModal(false)}>Cancel</button>
            </div>
          </div>
        </div>
      )}

      {/* Context Menu */}
      {contextMenu && (
        <div
          className="context-menu"
          style={{ left: contextMenu.x, top: contextMenu.y }}
          onClick={e => e.stopPropagation()}
        >
          {contextMenu.type === 'category' && (
            <>
              <div className="context-menu-item" onClick={() => {
                handleEditCategory(contextMenu.item)
                setContextMenu(null)
              }}>
                ✏️ Edit
              </div>
              <div className="context-menu-item" onClick={() => {
                handleDeleteCategory(contextMenu.item.id)
                setContextMenu(null)
              }}>
                🗑️ Delete
              </div>
            </>
          )}
          {contextMenu.type === 'wallet' && (
            <>
              <div className="context-menu-header">Move to:</div>
              {categories.map(cat => (
                <div
                  key={cat.id}
                  className="context-menu-item"
                  onClick={() => {
                    handleMoveWalletToCategory(contextMenu.item.address.toLowerCase(), cat.id)
                    setContextMenu(null)
                  }}
                >
                  <div className="category-color-small" style={{ backgroundColor: cat.color }} />
                  {cat.name}
                </div>
              ))}
              <div
                className="context-menu-item"
                onClick={() => {
                  handleMoveWalletToCategory(contextMenu.item.address.toLowerCase(), null)
                  setContextMenu(null)
                }}
              >
                Remove from category
              </div>
            </>
          )}
        </div>
      )}
    </div>
  )
}

export default WalletSidebar
