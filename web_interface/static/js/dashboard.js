document.addEventListener('DOMContentLoaded', () => {
    // DOM elements
    const userSelect = document.getElementById('user-select');
    const userTasksCount = document.getElementById('user-tasks-count');
    const statusFilter = document.getElementById('status-filter');
    const phoneFilter = document.getElementById('phone-filter');
    const nameFilter = document.getElementById('name-filter');
    const refreshBtn = document.getElementById('refresh-btn');
    const tasksContainer = document.getElementById('tasks-container');
    const loading = document.getElementById('loading');
    const tasksTableContainer = document.getElementById('tasks-table-container');
    const emptyState = document.getElementById('empty-state');
    const tasksTableBody = document.getElementById('tasks-tbody');
    const pagination = document.getElementById('pagination');
    const pageInfo = document.getElementById('page-info');
    const prevPageBtn = document.getElementById('prev-page');
    const nextPageBtn = document.getElementById('next-page');
    const deleteModal = document.getElementById('delete-modal');

    // State
    let currentUser = null;
    let currentPage = 1;
    let totalPages = 1;
    let taskToDelete = null;
    let expandedTasks = new Set();
    let hitlPollingInterval = null;
    let currentHitlRequest = null;
    let hitlTimeoutInterval = null;

    // Initialize
    loadUsers();
    startHitlPolling();

    // Event listeners
    userSelect.addEventListener('change', handleUserChange);
    statusFilter.addEventListener('change', loadTasks);
    phoneFilter.addEventListener('input', debounce(loadTasks, 300));
    nameFilter.addEventListener('input', debounce(loadTasks, 300));
    refreshBtn.addEventListener('click', loadTasks);
    prevPageBtn.addEventListener('click', () => changePage(currentPage - 1));
    nextPageBtn.addEventListener('click', () => changePage(currentPage + 1));

    // Delete modal event listeners
    const deleteModalClose = document.getElementById('delete-modal-close');
    const deleteCancelBtn = document.getElementById('delete-cancel-btn');
    const deleteConfirmBtn = document.getElementById('delete-confirm-btn');
    
    if (deleteModalClose) {
        deleteModalClose.addEventListener('click', closeDeleteModal);
    }
    if (deleteCancelBtn) {
        deleteCancelBtn.addEventListener('click', closeDeleteModal);
    }
    if (deleteConfirmBtn) {
        deleteConfirmBtn.addEventListener('click', confirmDelete);
    }

    // HITL modal event listeners
    const hitlModalClose = document.getElementById('hitl-modal-close');
    const hitlCancelBtn = document.getElementById('hitl-cancel-btn');
    const hitlSubmitBtn = document.getElementById('hitl-submit-btn');
    
    if (hitlModalClose) {
        hitlModalClose.addEventListener('click', closeHitlModal);
    }
    if (hitlCancelBtn) {
        hitlCancelBtn.addEventListener('click', closeHitlModal);
    }
    if (hitlSubmitBtn) {
        hitlSubmitBtn.addEventListener('click', submitHitlResponse);
    }

    // Database clear functionality
    const clearDatabaseBtn = document.getElementById('clear-database-btn');
    const clearDatabaseModal = document.getElementById('clear-database-modal');
    const cancelClearBtn = document.getElementById('cancel-clear-btn');
    const confirmClearBtn = document.getElementById('confirm-clear-btn');
    const clearConfirmInput = document.getElementById('clear-confirm-input');
    const clearProgress = document.getElementById('clear-progress');

    if (clearDatabaseBtn) {
        clearDatabaseBtn.addEventListener('click', function() {
            clearDatabaseModal.style.display = 'block';
        });
    }

    if (cancelClearBtn) {
        cancelClearBtn.addEventListener('click', function() {
            clearDatabaseModal.style.display = 'none';
            clearConfirmInput.value = '';
            confirmClearBtn.disabled = true;
        });
    }

    if (clearConfirmInput) {
        clearConfirmInput.addEventListener('input', function() {
            confirmClearBtn.disabled = this.value !== 'CONFIRM';
        });
    }

    if (confirmClearBtn) {
        confirmClearBtn.addEventListener('click', async function() {
            clearProgress.style.display = 'block';
            
            try {
                const response = await fetch('/api/clear-database?confirm=CONFIRM', {
                    method: 'DELETE'
                });
                
                const result = await response.json();
                
                if (response.ok && result.success) {
                    showNotification(`Database cleared successfully! Backup: ${result.backup_created}`, 'success');
                    // Clear current user selection and reload
                    currentUser = null;
                    userSelect.value = '';
                    loadUsers();
                    showEmptyState();
                } else {
                    showNotification(`Error: ${result.detail || result.message || 'Unknown error'}`, 'error');
                }
            } catch (error) {
                showNotification(`Failed to clear database: ${error.message}`, 'error');
            } finally {
                clearProgress.style.display = 'none';
                clearDatabaseModal.style.display = 'none';
                clearConfirmInput.value = '';
                confirmClearBtn.disabled = true;
            }
        });
    }

    // Close clear database modal when clicking outside
    if (clearDatabaseModal) {
        clearDatabaseModal.addEventListener('click', function(e) {
            if (e.target === clearDatabaseModal) {
                clearDatabaseModal.style.display = 'none';
                clearConfirmInput.value = '';
                confirmClearBtn.disabled = true;
            }
        });
    }

    // Load users for the dropdown
    async function loadUsers() {
        try {
            const response = await fetch('/api/users');
            const data = await response.json();
            
            if (data.success) {
                userSelect.innerHTML = '<option value="">Select a user...</option>';
                data.users.forEach(user => {
                    const option = document.createElement('option');
                    option.value = user.id;
                    option.textContent = user.username;
                    userSelect.appendChild(option);
                });
            }
        } catch (error) {
            console.error('Error loading users:', error);
            showNotification('Error loading users', 'error');
        }
    }

    // Handle user selection change
    function handleUserChange() {
        const selectedUserId = userSelect.value;
        if (selectedUserId) {
            currentUser = parseInt(selectedUserId);
            currentPage = 1;
            loadTasks();
        } else {
            currentUser = null;
            showEmptyState();
        }
    }

    // Load tasks for the selected user
    async function loadTasks() {
        if (!currentUser) {
            showEmptyState();
            return;
        }

        showLoading();

        try {
            const params = new URLSearchParams({
                user_id: currentUser,
                page: currentPage,
                page_size: 20
            });

            if (statusFilter.value) params.append('status', statusFilter.value);
            if (phoneFilter.value) params.append('phone', phoneFilter.value);
            if (nameFilter.value) params.append('name', nameFilter.value);

            const response = await fetch(`/api/tasks?${params}`);
            const data = await response.json();

            if (data.success) {
                displayTasks(data.tasks);
                updatePagination(data.pagination);
                updateTasksCount(data.pagination.total_count);
            } else {
                showNotification('Error loading tasks', 'error');
            }
        } catch (error) {
            console.error('Error loading tasks:', error);
            showNotification('Error loading tasks', 'error');
        }
    }

    // Display tasks in the table
    function displayTasks(tasks) {
        if (tasks.length === 0) {
            showEmptyState();
            return;
        }

        tasksTableBody.innerHTML = '';
        
        tasks.forEach(task => {
            const row = createTaskRow(task);
            tasksTableBody.appendChild(row);
        });

        showTasksTable();
        
        // Add event listeners for delete buttons
        document.querySelectorAll('.delete-task-btn').forEach(button => {
            button.addEventListener('click', function() {
                const taskId = this.getAttribute('data-task-id');
                const personName = this.getAttribute('data-person-name');
                const phoneNumber = this.getAttribute('data-phone-number');
                const status = this.getAttribute('data-status');
                showDeleteModal(taskId, personName, phoneNumber, status);
            });
        });

        // Add event listeners for expand buttons
        document.querySelectorAll('.expand-btn').forEach(button => {
            button.addEventListener('click', function() {
                const taskId = this.getAttribute('data-task-id');
                toggleTaskDetails(parseInt(taskId));
            });
        });
    }

    // Create a task row
    function createTaskRow(task) {
        const row = document.createElement('tr');
        row.className = 'task-row';
        row.id = `task-row-${task.id}`;

        const statusBadge = `<span class="status-badge status-${task.status.replace(/_/g, '-')}">${task.status.replace(/_/g, ' ')}</span>`;
        
        const attemptsPercentage = (task.current_attempt_count / task.max_attempts) * 100;
        const attemptsClass = attemptsPercentage >= 80 ? 'danger' : attemptsPercentage >= 60 ? 'warning' : '';
        
        const nextActionTime = task.next_action_time ? 
            new Date(task.next_action_time).toLocaleString() : 
            'Not scheduled';

        const createdTime = new Date(task.created_at).toLocaleString();

        row.innerHTML = `
            <td>${task.id}</td>
            <td>
                <div class="task-info">
                    <strong>${task.person_name}</strong>
                    <br>
                    <small>${task.user_task_description.substring(0, 50)}...</small>
                </div>
            </td>
            <td>${task.phone_number}</td>
            <td>${statusBadge}</td>
            <td>
                <div class="attempts-info">
                    <span>${task.current_attempt_count}/${task.max_attempts}</span>
                    <div class="attempts-bar">
                        <div class="attempts-fill ${attemptsClass}" style="width: ${attemptsPercentage}%"></div>
                    </div>
                </div>
            </td>
            <td>${nextActionTime}</td>
            <td>${createdTime}</td>
            <td>
                <div class="task-actions">
                    <button class="btn btn-small btn-secondary expand-btn" data-task-id="${task.id}">
                        Details
                    </button>
                    <button class="btn btn-small btn-danger delete-task-btn"
                            data-task-id="${task.id}"
                            data-person-name="${task.person_name}"
                            data-phone-number="${task.phone_number}"
                            data-status="${task.status}">
                        Delete
                    </button>
                </div>
            </td>
        `;

        return row;
    }

    // Toggle task details
    async function toggleTaskDetails(taskId) {
        const row = document.getElementById(`task-row-${taskId}`);
        const existingDetails = document.getElementById(`task-details-${taskId}`);

        if (existingDetails) {
            existingDetails.remove();
            row.classList.remove('expanded');
            expandedTasks.delete(taskId);
            return;
        }

        try {
            const response = await fetch(`/api/tasks/${taskId}/calls`);
            const data = await response.json();

            if (data.success) {
                const detailsRow = document.createElement('tr');
                detailsRow.id = `task-details-${taskId}`;
                detailsRow.innerHTML = `
                    <td colspan="8">
                        <div class="task-details show">
                            <h4>Call History</h4>
                            <div class="calls-list">
                                ${data.calls.map(call => `
                                    <div class="call-item">
                                        <div class="call-header">
                                            <strong>Attempt ${call.attempt_number}</strong>
                                            <span class="call-status status-${call.status.replace(/_/g, '-')}">${call.status.replace(/_/g, ' ')}</span>
                                        </div>
                                        <div class="call-info">
                                            <strong>Started:</strong> ${new Date(call.created_at).toLocaleString()}<br>
                                            ${call.duration_seconds ? `<strong>Duration:</strong> ${call.duration_seconds}s<br>` : ''}
                                            ${call.hangup_cause ? `<strong>Hangup Cause:</strong> ${call.hangup_cause}<br>` : ''}
                                            ${call.call_conclusion ? `<strong>Conclusion:</strong> ${call.call_conclusion}` : ''}
                                        </div>
                                    </div>
                                `).join('')}
                            </div>
                        </div>
                    </td>
                `;

                row.insertAdjacentElement('afterend', detailsRow);
                row.classList.add('expanded');
                expandedTasks.add(taskId);
            }
        } catch (error) {
            console.error('Error loading task details:', error);
            showNotification('Error loading task details', 'error');
        }
    }

    // Show delete confirmation modal
    function showDeleteModal(taskId, contactName, phoneNumber, status) {
        taskToDelete = taskId;
        document.getElementById('delete-contact-name').textContent = contactName;
        document.getElementById('delete-phone-number').textContent = phoneNumber;
        document.getElementById('delete-status').textContent = status;
        deleteModal.style.display = 'block';
    }

    // Close delete modal
    function closeDeleteModal() {
        deleteModal.style.display = 'none';
        taskToDelete = null;
    }

    // Confirm delete
    async function confirmDelete() {
        if (!taskToDelete) return;

        try {
            const response = await fetch(`/api/tasks/${taskToDelete}`, {
                method: 'DELETE'
            });

            const data = await response.json();

            if (data.success) {
                showNotification('Task deleted successfully', 'success');
                closeDeleteModal();
                loadTasks(); // Refresh the task list
            } else {
                showNotification('Error deleting task', 'error');
            }
        } catch (error) {
            console.error('Error deleting task:', error);
            showNotification('Error deleting task', 'error');
        }
    }

    // Close modal when clicking outside
    window.addEventListener('click', function(event) {
        if (event.target === deleteModal) {
            closeDeleteModal();
        }
    });

    // Update pagination
    function updatePagination(paginationData) {
        currentPage = paginationData.page;
        totalPages = paginationData.total_pages;

        pageInfo.textContent = `Page ${currentPage} of ${totalPages}`;
        prevPageBtn.disabled = currentPage <= 1;
        nextPageBtn.disabled = currentPage >= totalPages;

        pagination.style.display = totalPages > 1 ? 'flex' : 'none';
    }

    // Change page
    function changePage(page) {
        if (page >= 1 && page <= totalPages) {
            currentPage = page;
            loadTasks();
        }
    }

    // Update tasks count
    function updateTasksCount(count) {
        userTasksCount.textContent = `(${count} tasks)`;
    }

    // Show loading state
    function showLoading() {
        loading.style.display = 'block';
        tasksTableContainer.style.display = 'none';
        emptyState.style.display = 'none';
        pagination.style.display = 'none';
    }

    // Show tasks table
    function showTasksTable() {
        loading.style.display = 'none';
        tasksTableContainer.style.display = 'block';
        emptyState.style.display = 'none';
    }

    // Show empty state
    function showEmptyState() {
        loading.style.display = 'none';
        tasksTableContainer.style.display = 'none';
        emptyState.style.display = 'block';
        pagination.style.display = 'none';
        userTasksCount.textContent = '';
    }

    // Show notification
    function showNotification(message, type = 'info') {
        const notification = document.createElement('div');
        notification.className = `notification notification-${type}`;
        notification.style.cssText = `
            position: fixed;
            top: 20px;
            right: 20px;
            padding: 15px 20px;
            border-radius: 4px;
            color: white;
            font-weight: 500;
            z-index: 1000;
            max-width: 300px;
        `;
        
        switch (type) {
            case 'success':
                notification.style.backgroundColor = '#28a745';
                break;
            case 'error':
                notification.style.backgroundColor = '#dc3545';
                break;
            case 'warning':
                notification.style.backgroundColor = '#ffc107';
                notification.style.color = '#212529';
                break;
            default:
                notification.style.backgroundColor = '#17a2b8';
        }
        
        notification.innerHTML = `
            <span>${message}</span>
            <button class="notification-close-btn" style="background: none; border: none; color: inherit; float: right; font-size: 16px; cursor: pointer; margin-left: 10px;">×</button>
        `;
        
        // Add event listener for close button
        const closeBtn = notification.querySelector('.notification-close-btn');
        if (closeBtn) {
            closeBtn.addEventListener('click', () => notification.remove());
        }
        
        document.body.appendChild(notification);
        
        setTimeout(() => {
            if (notification.parentElement) {
                notification.remove();
            }
        }, 5000);
    }

    // Debounce function
    function debounce(func, wait) {
        let timeout;
        return function executedFunction(...args) {
            const later = () => {
                clearTimeout(timeout);
                func(...args);
            };
            clearTimeout(timeout);
            timeout = setTimeout(later, wait);
        };
    }

    // HITL Functionality
    function startHitlPolling() {
        if (hitlPollingInterval) {
            clearInterval(hitlPollingInterval);
        }
        
        hitlPollingInterval = setInterval(() => {
            if (currentUser) {
                checkForHitlRequests();
            }
        }, 2000); // Check every 2 seconds
    }

    async function checkForHitlRequests() {
        if (!currentUser) return;
        
        try {
            const userSelect = document.getElementById('user-select');
            const selectedOption = userSelect.options[userSelect.selectedIndex];
            const username = selectedOption ? selectedOption.textContent : '';
            
            if (!username) return;
            
            const response = await fetch(`/api/pending_hitl_requests?username=${encodeURIComponent(username)}`);
            const data = await response.json();
            
            if (data.success && data.requests.length > 0) {
                displayHitlRequests(data.requests);
                // Show the first request in modal
                if (!currentHitlRequest) {
                    showHitlRequestModal(data.requests[0], username);
                }
            } else {
                hideHitlNotifications();
            }
        } catch (error) {
            console.error('Error checking for HITL requests:', error);
        }
    }

    function displayHitlRequests(requests) {
        const hitlSection = document.getElementById('hitl-notifications');
        const container = document.getElementById('hitl-requests-container');
        
        if (requests.length === 0) {
            hitlSection.style.display = 'none';
            return;
        }
        
        container.innerHTML = '';
        
        requests.forEach(request => {
            const requestDiv = document.createElement('div');
            requestDiv.className = 'hitl-request-item';
            requestDiv.innerHTML = `
                <div class="hitl-request-info">
                    <h4>Call in Progress - Task ${request.task_id}</h4>
                    <p><strong>Contact:</strong> ${request.call_info.person_name || 'Unknown'} - ${request.call_info.phone_number}</p>
                    <p><strong>Business:</strong> ${request.call_info.business_name || 'N/A'}</p>
                    <div class="hitl-request-question">
                        <strong>Question:</strong> ${request.question}
                    </div>
                </div>
                <div class="hitl-request-actions">
                    <button class="btn btn-success respond-hitl-btn" data-task-id="${request.task_id}">
                        Respond
                    </button>
                </div>
            `;
            container.appendChild(requestDiv);
        });
        
        hitlSection.style.display = 'block';
        
        // Add event listeners for respond buttons
        document.querySelectorAll('.respond-hitl-btn').forEach(button => {
            button.addEventListener('click', function() {
                const taskId = parseInt(this.getAttribute('data-task-id'));
                respondToHitlRequest(taskId);
            });
        });
        
        // Add notification badge
        addHitlNotificationBadge(requests.length);
    }

    function addHitlNotificationBadge(count) {
        // Remove existing badge
        const existingBadge = document.querySelector('.hitl-notification-badge');
        if (existingBadge) {
            existingBadge.remove();
        }
        
        // Add new badge
        const badge = document.createElement('div');
        badge.className = 'hitl-notification-badge';
        badge.textContent = `${count} Call${count > 1 ? 's' : ''} Need Response`;
        badge.addEventListener('click', () => {
            document.getElementById('hitl-notifications').scrollIntoView({ behavior: 'smooth' });
        });
        document.body.appendChild(badge);
    }

    function hideHitlNotifications() {
        const hitlSection = document.getElementById('hitl-notifications');
        hitlSection.style.display = 'none';
        
        // Remove notification badge
        const badge = document.querySelector('.hitl-notification-badge');
        if (badge) {
            badge.remove();
        }
    }

    async function respondToHitlRequest(taskId) {
        try {
            const userSelect = document.getElementById('user-select');
            const selectedOption = userSelect.options[userSelect.selectedIndex];
            const username = selectedOption ? selectedOption.textContent : '';
            
            if (!username) return;
            
            const response = await fetch(`/api/pending_hitl_requests?username=${encodeURIComponent(username)}`);
            const data = await response.json();
            
            if (data.success) {
                const request = data.requests.find(r => r.task_id === taskId);
                if (request) {
                    showHitlRequestModal(request, username);
                }
            }
        } catch (error) {
            console.error('Error responding to HITL request:', error);
        }
    }

    function showHitlRequestModal(request, username) {
        currentHitlRequest = { ...request, username };
        
        document.getElementById('hitl-contact-name').textContent = request.call_info.person_name || 'Unknown';
        document.getElementById('hitl-phone-number').textContent = request.call_info.phone_number || 'Unknown';
        document.getElementById('hitl-business-name').textContent = request.call_info.business_name || 'N/A';
        document.getElementById('hitl-question').textContent = request.question;
        document.getElementById('hitl-response-input').value = '';
        
        // Start countdown timer
        startHitlCountdown(request.timeout_seconds);
        
        document.getElementById('hitl-response-modal').style.display = 'block';
        document.getElementById('hitl-response-input').focus();
    }

    function startHitlCountdown(timeoutSeconds) {
        let timeLeft = timeoutSeconds;
        const countdownElement = document.getElementById('hitl-timeout-countdown');
        
        if (hitlTimeoutInterval) {
            clearInterval(hitlTimeoutInterval);
        }
        
        hitlTimeoutInterval = setInterval(() => {
            countdownElement.textContent = timeLeft;
            
            if (timeLeft <= 10) {
                countdownElement.className = 'hitl-timeout-warning';
            }
            
            if (timeLeft <= 0) {
                clearInterval(hitlTimeoutInterval);
                closeHitlModal();
                showNotification('HITL request timed out', 'warning');
            }
            
            timeLeft--;
        }, 1000);
    }

    function closeHitlModal() {
        document.getElementById('hitl-response-modal').style.display = 'none';
        currentHitlRequest = null;
        
        if (hitlTimeoutInterval) {
            clearInterval(hitlTimeoutInterval);
            hitlTimeoutInterval = null;
        }
    }

    async function submitHitlResponse() {
        if (!currentHitlRequest) return;
        
        const response = document.getElementById('hitl-response-input').value.trim();
        if (!response) {
            showNotification('Please enter a response', 'warning');
            return;
        }
        
        try {
            const submitResponse = await fetch('/api/hitl_response', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    task_id: currentHitlRequest.task_id,
                    response: response,
                    username: currentHitlRequest.username
                })
            });
            
            const result = await submitResponse.json();
            
            if (result.success) {
                showNotification('Response sent successfully!', 'success');
                closeHitlModal();
                // Refresh HITL requests
                checkForHitlRequests();
            } else {
                showNotification(`Error: ${result.message}`, 'error');
            }
        } catch (error) {
            console.error('Error submitting HITL response:', error);
            showNotification('Error submitting response', 'error');
        }
    }

    // Close HITL modal when clicking outside
    window.addEventListener('click', function(event) {
        const hitlModal = document.getElementById('hitl-response-modal');
        if (event.target === hitlModal) {
            closeHitlModal();
        }
    });

    // Handle Enter key in HITL response input
    document.addEventListener('keydown', function(event) {
        if (event.key === 'Enter' && event.ctrlKey) {
            const hitlModal = document.getElementById('hitl-response-modal');
            if (hitlModal.style.display === 'block') {
                submitHitlResponse();
            }
        }
    });
});