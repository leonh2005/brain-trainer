class Plant {
  final int? id;
  final String name;
  final String category;
  final String edibleParts;
  final String notes;
  final double lat;
  final double lng;
  final List<String> photoUrls;
  final String createdAt;

  const Plant({
    this.id,
    required this.name,
    required this.category,
    required this.edibleParts,
    required this.notes,
    required this.lat,
    required this.lng,
    this.photoUrls = const [],
    this.createdAt = '',
  });

  factory Plant.fromJson(Map<String, dynamic> json) => Plant(
        id: json['id'],
        name: json['name'] ?? '',
        category: json['category'] ?? '',
        edibleParts: json['edible_parts'] ?? '',
        notes: json['notes'] ?? '',
        lat: (json['lat'] as num).toDouble(),
        lng: (json['lng'] as num).toDouble(),
        photoUrls: List<String>.from(json['photo_urls'] ?? []),
        createdAt: json['created_at'] ?? '',
      );

  Map<String, dynamic> toJson() => {
        'name': name,
        'category': category,
        'edible_parts': edibleParts,
        'notes': notes,
        'lat': lat,
        'lng': lng,
      };
}

const kPlantCategories = ['草本', '木本', '藤本', '水生', '其他'];
