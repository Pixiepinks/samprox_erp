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
