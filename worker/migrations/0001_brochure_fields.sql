-- Brochure feature: fields needed to render the PDF brochure template
ALTER TABLE properties ADD COLUMN room_count INTEGER;
ALTER TABLE properties ADD COLUMN parking_spaces INTEGER;
ALTER TABLE properties ADD COLUMN condition_rating TEXT;
ALTER TABLE properties ADD COLUMN dpe_rating TEXT;
ALTER TABLE properties ADD COLUMN terrace_description TEXT;
ALTER TABLE properties ADD COLUMN availability_period TEXT;
ALTER TABLE properties ADD COLUMN reference_number TEXT;
ALTER TABLE properties ADD COLUMN rental_type TEXT;
ALTER TABLE properties ADD COLUMN listing_type_label TEXT;
