# SAMPROX ERP – Material Receipt Notes

## Database setup

1. Apply database migrations:

   ```bash
   flask db upgrade
   ```

2. Seed the default material categories and types:

   ```bash
   flask seed-materials
   ```

## Material Receipt Note workflow

- Navigate to **Material → MRN → New** at `/material/mrn/new` to create a Material Receipt Note.
- After saving, you will be redirected to the read-only MRN view at `/material/mrn/<mrn_id>` where you can print the note.

## Sales Visit Tracking

- Apply migrations and access the Sales Visits portal at `/sales_visits` (visible to Sales, Outside Managers, and Admins). Use the **Sales Visits** button in the portal header to navigate.
- Sales users can create visits for themselves, check in/out with GPS coordinates, and add attachments. Exceptions (GPS mismatch, short duration, manual overrides) automatically trigger approval.
- Outside Managers can view only team members mapped via `/api/sales-visits/team`, and can approve/reject pending exceptions.
- Admins can view all visits, reassign ownership, manage team mappings, and override approvals.
