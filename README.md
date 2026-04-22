# Park Guide App Backend

Django REST backend for the Park Guide App training platform. This service handles authentication, training content, learner progress, badges, notifications, and secure file delivery.

## Stack
- Django + Django REST Framework
- JWT authentication (SimpleJWT)
- Neon PostgreSQL
- Custom user model (`accounts.CustomUser`)
- Firebase secure file storage

## Features
- Email-based registration and login
- Training courses and modules API
- Module completion and course progress tracking
- Badge progress and awarded badge endpoints
- In-app notifications with read/clear actions
- Secure file upload, download, and temporary signed URLs using Firebase Storage
- Django admin for courses, badges, notifications, users, and files

## How to run?
```bash
python venv venv
python manage.py runserver
```

## Environment
This backend uses a shared online database and shared project services.
Local setup, migration, and bootstrap steps are intentionally omitted from this README.

## Secure File Endpoints (Firebase Storage)
- `GET /api/secure-files/files/` – list your uploaded files (admin sees all)
- `POST /api/secure-files/files/` – upload file with multipart field `file`
- `GET /api/secure-files/files/{id}/` – file metadata + temporary download URL
- `GET /api/secure-files/files/{id}/download-url/` – new temporary download URL
- `DELETE /api/secure-files/files/{id}/` – delete a file

## Firebase Storage
Secure file uploads are stored in Firebase Storage.

## API Overview
Base routes:
- `/api/`
- `/api/accounts/`
- `/api/notifications/`
- `/api/user-progress/`
- `/api/secure-files/`

Authentication:
- `POST /api/accounts/register/`
- `POST /api/accounts/login/`
- `POST /api/accounts/passkeys/login/options/`
- `POST /api/accounts/passkeys/login/verify/`
- `GET /api/accounts/passkeys/status/`
- `POST /api/accounts/passkeys/register/options/`
- `POST /api/accounts/passkeys/register/verify/`
- `POST /api/accounts/passkeys/disable/`
- `POST /api/accounts/token/refresh/`

Training:
- `GET /api/courses/`
- `GET /api/modules/`
- `GET /api/progress/`
- `POST /api/progress/`
- `GET /api/course-progress/`
- `POST /api/course-progress/`
- `POST /api/complete-module/`

Badges:
- `GET /api/user-progress/badges/`
- `GET /api/user-progress/my-badges/`

Notifications:
- `GET /api/notifications/items/`
- `POST /api/notifications/items/{id}/mark-read/`
- `POST /api/notifications/items/mark-all-read/`
- `POST /api/notifications/items/clear-read/`

Secure files:
- `GET /api/secure-files/files/`
- `POST /api/secure-files/files/` with multipart field `file`
- `GET /api/secure-files/files/{id}/`
- `DELETE /api/secure-files/files/{id}/`
- `GET /api/secure-files/files/{id}/download-url/`
- `GET /api/secure-files/files/{id}/download/`

All API endpoints require `Authorization: Bearer <access_token>` unless noted otherwise.

## Admin
Admin URL:
- `/admin/`

Main admin areas include:
- Accounts
- Courses and modules
- User progress
- Badges and awarded badges
- Notifications
- Secure files

Notification send flow:
1. Create a notification in Django admin.
2. Select it in the changelist.
3. Run the action to send it to users.

Available sections under Notifications:
- Notification
- User notification

Admin send flow:
1. Create a Notification in Django admin.
2. Select it from list view.
3. Run action: **Send selected notifications to all users**.

## Notes
- `ModuleProgress` and `CourseProgress` are the source of truth for learner progress.
- Admins can create badges and manage them with a pending workflow (`pending`, `granted`, `rejected`) based on each user's completed module count.
- Admin actions support syncing pending badges for eligible users, auto-approving pending badges, and auto-rejecting pending badges.
- Admins can also use a one-click action: **Sync pending then auto approve eligible users**.
- Notifications can be broadcast from admin to all regular app users in one action (excludes staff/admin accounts).
- New notifications created from admin are auto-broadcast immediately to all regular app users (no second step needed).
- Quiz data exists inside module content (`Module.quiz`) and now supports multiple quizzes per module.
- Training JSON can use either `quiz` (single object, backward compatible) or `quizzes` (array of quiz objects).
- Each quiz supports single-answer (`correctIndex`) and multi-answer (`correctIndexes`) with up to 3 correct choices.
- Posting to progress endpoints reuses and amends existing progress records for the same user/course or user/module instead of creating new IDs.
- Dependencies are maintained in `requirements.txt` and should stay project-focused only.
- Secure files are stored in Firebase private storage and accessed only with valid app auth + short-lived signed URLs.
