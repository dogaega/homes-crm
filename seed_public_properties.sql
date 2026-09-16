INSERT INTO properties (
  id, property_id, slug, address, city, state, zip_code, price, bedrooms, bathrooms,
  square_feet, lot_size, plot_size, year_built, floor_count, property_type, listing_status,
  description, terrain_description, house_history, concept_description,
  construction_details, engineering_details, room_layout, floor_plan_urls, gallery_urls,
  video_url, photos, map_lat, map_lng, featured, public_listing,
  assigned_agent_id, created_by, listing_date, created_at, updated_at
) VALUES (
  'sample-villa-cap-dail',
  'PROP-PUB-001',
  'sample-villa-cap-dail',
  'Route de la Corniche', 'Cap d''Ail', 'PACA', '06320',
  8500000, 5, 5, 420, 1800, 1800, 2018, 2, 'villa', 'active',
  'A placeholder example listing for the public marketing site. Not a real property or price.',
  'South-facing terraced plot with mature olive trees and direct sea views over the Mediterranean.',
  'This example villa illustrates the long-form "house history" narrative section used on the public site — a place to describe the story of a build, prior renovations, and its place in the neighbourhood.',
  'An open, light-filled concept connecting indoor living spaces to a series of outdoor terraces, designed around the sea view.',
  '{"walls":"Reinforced concrete with natural stone cladding","insulation":"High-performance mineral wool, RT2012 compliant","roof":"Flat roof, waterproof membrane, accessible terrace","windows":"Triple-glazed aluminium, floor-to-ceiling"}',
  '{"heating":"Reversible air-to-water heat pump","underfloor_heating":"Yes, throughout","ventilation":"Double-flow mechanical (VMC)","water_supply":"Municipal mains","sewage":"Municipal mains","backup_power":"Diesel generator, automatic transfer switch"}',
  '[{"name":"Living / dining","description":"Open-plan, sea-facing, 85 sq m"},{"name":"Kitchen","description":"Fully equipped, opens to terrace"},{"name":"Master suite","description":"Sea view, dressing room, ensuite bathroom"},{"name":"Guest bedrooms","description":"Four additional en-suite bedrooms"},{"name":"Wellness","description":"Spa, sauna, gym on garden level"}]',
  '["https://images.unsplash.com/photo-1600585154340-be6161a56a0c?w=1600"]',
  '["https://images.unsplash.com/photo-1600596542815-ffad4c1539a9?w=1600","https://images.unsplash.com/photo-1600607687939-ce8a6c25118c?w=1600","https://images.unsplash.com/photo-1600607687644-aac4c3eac7f4?w=1600","https://images.unsplash.com/photo-1600585154526-990dced4db0d?w=1600"]',
  NULL,
  '["https://images.unsplash.com/photo-1600585154340-be6161a56a0c?w=1600"]',
  43.7245, 7.4079,
  1, 1,
  '36da52cd-2dd9-429d-989e-77167b17dcd5', '36da52cd-2dd9-429d-989e-77167b17dcd5',
  '2026-09-01T00:00:00.000Z', '2026-09-01T00:00:00.000Z', '2026-09-01T00:00:00.000Z'
);

INSERT INTO properties (
  id, property_id, slug, address, city, state, zip_code, price, bedrooms, bathrooms,
  square_feet, lot_size, plot_size, year_built, floor_count, property_type, listing_status,
  description, terrain_description, house_history, concept_description,
  construction_details, engineering_details, room_layout, floor_plan_urls, gallery_urls,
  video_url, photos, map_lat, map_lng, featured, public_listing,
  assigned_agent_id, created_by, listing_date, created_at, updated_at
) VALUES (
  'sample-villa-eze-hills',
  'PROP-PUB-002',
  'sample-villa-eze-hills',
  'Chemin des Hauts', 'Eze', 'PACA', '06360',
  6200000, 4, 4, 310, 1200, 1200, 2020, 3, 'villa', 'active',
  'A second placeholder example listing for the public marketing site. Not a real property or price.',
  'Elevated hillside plot terraced into the rock, panoramic coastal views from Cap Ferrat to Italy.',
  'A second example narrative describing the property''s design intent and construction period, used to demonstrate the detail template with multiple listings.',
  'A stacked, view-oriented layout across three levels, each opening onto its own terrace.',
  '{"walls":"Poured concrete, render finish","insulation":"Exterior insulation system (ETICS)","roof":"Pitched, natural stone tile","windows":"Double-glazed aluminium"}',
  '{"heating":"Geothermal heat pump","underfloor_heating":"Yes, ground and first floor","ventilation":"Double-flow mechanical (VMC)","water_supply":"Municipal mains","sewage":"Municipal mains","backup_power":"None"}',
  '[{"name":"Living / dining","description":"Double-height, panoramic glazing"},{"name":"Kitchen","description":"Island kitchen with terrace access"},{"name":"Master suite","description":"Top floor, private terrace"},{"name":"Guest bedrooms","description":"Three bedrooms, garden level"}]',
  '["https://images.unsplash.com/photo-1600566753190-17f0baa2a6c3?w=1600"]',
  '["https://images.unsplash.com/photo-1600607688969-a5bfcd646154?w=1600","https://images.unsplash.com/photo-1600566752355-35792bedcfea?w=1600","https://images.unsplash.com/photo-1600047509807-ba8f99d2cdde?w=1600"]',
  NULL,
  '["https://images.unsplash.com/photo-1600566753190-17f0baa2a6c3?w=1600"]',
  43.7298, 7.3617,
  0, 1,
  '36da52cd-2dd9-429d-989e-77167b17dcd5', '36da52cd-2dd9-429d-989e-77167b17dcd5',
  '2026-09-05T00:00:00.000Z', '2026-09-05T00:00:00.000Z', '2026-09-05T00:00:00.000Z'
);
