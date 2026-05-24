import 'dart:convert';
import 'dart:io';
import 'package:http/http.dart' as http;
import '../models/plant.dart';

// 手機和 Mac 需在同一個 Wi-Fi
const String kBaseUrl = 'http://192.168.68.123:5800';

class ApiService {
  static final _client = http.Client();

  static Future<List<Plant>> getPlants() async {
    final res = await _client.get(Uri.parse('$kBaseUrl/api/plants'));
    if (res.statusCode != 200) throw Exception('載入失敗');
    final List data = jsonDecode(utf8.decode(res.bodyBytes));
    return data.map((j) => Plant.fromJson(j)).toList();
  }

  static Future<Plant> createPlant(Plant plant) async {
    final res = await _client.post(
      Uri.parse('$kBaseUrl/api/plants'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode(plant.toJson()),
    );
    if (res.statusCode != 201) throw Exception('新增失敗');
    return Plant.fromJson(jsonDecode(utf8.decode(res.bodyBytes)));
  }

  static Future<void> deletePlant(int id) async {
    final res = await _client.delete(Uri.parse('$kBaseUrl/api/plants/$id'));
    if (res.statusCode != 200) throw Exception('刪除失敗');
  }

  static Future<void> uploadPhoto(int plantId, File photo) async {
    final req = http.MultipartRequest(
      'POST',
      Uri.parse('$kBaseUrl/api/plants/$plantId/photos'),
    );
    req.files.add(await http.MultipartFile.fromPath('photo', photo.path));
    final res = await req.send();
    if (res.statusCode != 200) throw Exception('上傳失敗');
  }
}
