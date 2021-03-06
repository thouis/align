#include <iostream>
#include <string>
#include <vector>

#include <opencv2/core/core.hpp>
#include <opencv2/highgui/highgui.hpp>
#include <opencv2/features2d/features2d.hpp>
#include <opencv2/nonfree/nonfree.hpp>
#include <opencv2/legacy/legacy.hpp>
#include <opencv2/video/tracking.hpp>
#include <H5Cpp.h>

using namespace cv;
using namespace H5;
using namespace std;

H5File create_hdf5_file(char *filename);
H5File open_hdf5_file(char *filename);
void write_hdf5_image(H5File h5f, const char *name, const Mat &im);
void read_hdf5_image(H5File h5f, Mat &image_out, const char *name, const Rect &roi=Rect(0,0,0,0));

Point2f operator*(Mat M, const Point2f& p) {
    Mat src(3/*rows*/, 1 /* cols */, CV_64F); 
    src.at<double>(0, 0) = p.x; 
    src.at<double>(1, 0) = p.y; 
    src.at<double>(2, 0) = 1.0; 
    Mat dst = M*src;
    return Point2f(dst.at<double>(0,0),dst.at<double>(1,0)); 
}

Point2f operator*(Mat M, const KeyPoint& p) {
    return M * p.pt; 
}

static vector<int>
nearby_indices(int x, int y, int cur_blocksize, int end_block_size,
               map<pair<int, int>, vector<int> > bins)
{
    // round to nearest end_block_size
    x = end_block_size * (int) (((float) x) / end_block_size + 0.5);
    y = end_block_size * (int) (((float) y) / end_block_size + 0.5);
    vector<int> out;
    for (int dx = -cur_blocksize; dx <= cur_blocksize; dx += end_block_size)
        for (int dy = -cur_blocksize; dy <= cur_blocksize; dy += end_block_size) {
            pair<int, int> key(x + dx, y + dy);
            if (bins.count(key) > 0) {
                out.insert(out.end(), bins[key].begin(), bins[key].end());
            } 
        }
    return out;
}
            
#define OCTAVES 3

int main( int argc, char** argv ) {
    // check http://opencv.itseez.com/doc/tutorials/features2d/table_of_content_features2d/table_of_content_features2d.html
    // for OpenCV general detection/matching framework details

    // Load images
    double t = (double)getTickCount();
    Mat imgA = imread(argv[1], CV_LOAD_IMAGE_GRAYSCALE );
    if( !imgA.data ) {
        cout<< " --(!) Error reading image " << argv[1] << endl;
        return -1;
    }
    Mat imgB = imread(argv[2], CV_LOAD_IMAGE_GRAYSCALE );
    if( !imgB.data ) {
        cout << " --(!) Error reading image " << argv[2] << endl;
        return -1;
    }
    for (int i = 0; i < OCTAVES; i++) {
        medianBlur(imgA, imgA, 3);
        resize(imgA, imgA, Size(0, 0), 1.0 / 2, 1.0 / 2, INTER_CUBIC);
    }

    for (int i = 0; i < OCTAVES; i++) {
        medianBlur(imgB, imgB, 3);
        resize(imgB, imgB, Size(0, 0), 1.0 / 2, 1.0 / 2, INTER_CUBIC);
    }
    t = ((double)getTickCount() - t)/getTickFrequency();
    cout << "load time [s]: " << t << endl;

    // DETECTION
    // Any openCV detector such as
    // BRISK detector(20, 0, 1);
    // SurfFeatureDetector detector(1000,4);
    // MSER detector;
    // FastFeatureDetector detector(80, true);
    // StarDetector detector;
    SurfFeatureDetector detector( 1000, 4 );

    // DESCRIPTOR
    // Our proposed FREAK descriptor
    // (roation invariance, scale invariance, pattern radius corresponding to SMALLEST_KP_SIZE,
    // number of octaves, optional vector containing the selected pairs)
    // FREAK extractor(true, true, 22, 4, vector<int>());
    FREAK extractor(false, false, 25, 1);

    // MATCHER
    // The standard Hamming distance can be used such as
    // BruteForceMatcher<Hamming> matcher;
    // or the proposed cascade of hamming distance using SSSE3
    BruteForceMatcher<Hamming> matcher;

    // detect
    vector<KeyPoint> keypointsA, keypointsB;
    t = (double)getTickCount();
    detector.detect(imgA, keypointsA);
    detector.detect(imgB, keypointsB);
    cout << "Detected " << keypointsA.size() << ", " << keypointsB.size() << endl;
    t = ((double)getTickCount() - t)/getTickFrequency();
    cout << "detection time [s]: " << t/1.0 << endl;

    // extract features
    Mat descriptorsA, descriptorsB;
    t = (double)getTickCount();
    extractor.compute(imgA, keypointsA, descriptorsA);
    extractor.compute(imgB, keypointsB, descriptorsB);
    t = ((double)getTickCount() - t)/getTickFrequency();
    cout << "extraction (" << descriptorsA.rows << ", " << descriptorsB.rows << ") time [s]: " << t << endl;

    // match
    vector<DMatch> matches;
    t = (double)getTickCount();
    matcher.match(descriptorsA, descriptorsB, matches);
    cout << "Found matches " << matches.size() << endl;
    t = ((double)getTickCount() - t)/getTickFrequency();
    cout << "matching time [s]: " << t << endl;

    // compute median and MAD of matches
    vector<float> x_shifts, y_shifts;
    vector<float> match_distances;
    for (vector<DMatch>::iterator it = matches.begin(); it != matches.end(); it++) {
        Point2f delta = keypointsA[it->queryIdx].pt - keypointsB[it->trainIdx].pt;
        x_shifts.push_back(delta.x);
        y_shifts.push_back(delta.y);
        match_distances.push_back(norm(delta));
    }
    sort(x_shifts.begin(), x_shifts.end());
    sort(y_shifts.begin(), y_shifts.end());
    sort(match_distances.begin(), match_distances.end());
    cout << "X shifts " << x_shifts[x_shifts.size() / 4] << " " << x_shifts[x_shifts.size() / 2] << " " << x_shifts[(x_shifts.size() * 3) / 4] << endl;
    cout << "Y shifts " << y_shifts[y_shifts.size() / 4] << " " << y_shifts[y_shifts.size() / 2] << " " << y_shifts[(y_shifts.size() * 3) / 4] << endl;
    cout << "L2 " << match_distances[match_distances.size() / 4] << " " << match_distances[match_distances.size() / 2] << " " << match_distances[(match_distances.size() * 3) / 4] << endl;
    float median_X = x_shifts[x_shifts.size() / 2];
    float median_Y = y_shifts[y_shifts.size() / 2];
    float median_L2 = match_distances[match_distances.size() / 2];
    vector<float> abs_deviations_x, abs_deviations_y;
    for (int idx = 0; idx < matches.size(); idx++) {
        abs_deviations_x.push_back(abs(x_shifts[idx] - median_X));
        abs_deviations_y.push_back(abs(y_shifts[idx] - median_Y));
    }
    sort(abs_deviations_x.begin(), abs_deviations_x.end());
    sort(abs_deviations_y.begin(), abs_deviations_y.end());
    float MAD_X = abs_deviations_x[abs_deviations_x.size() / 2];
    float MAD_Y = abs_deviations_y[abs_deviations_y.size() / 2];
    cout << "MAD " << MAD_X << " " << MAD_Y << " " << sqrt(MAD_X * MAD_X + MAD_Y * MAD_Y) << endl;

    // filter for sane matches, those with <= 2 sigma_mad = 2 * 1.48 * MAD
    vector<DMatch> good_matches;
    for (vector<DMatch>::iterator it = matches.begin(); it != matches.end(); it++) {
        Point2f delta = keypointsA[it->queryIdx].pt - keypointsB[it->trainIdx].pt;
        if (abs(delta.x - median_X) <= 2.0 * 1.48 * MAD_X &&
            abs(delta.y - median_Y) <= 2.0 * 1.48 * MAD_Y)
            good_matches.push_back(*it);
    }

    cout << "Good matches " << good_matches.size() <<endl;

    H5File out_hdf5 = create_hdf5_file(argv[3]);
    Mat warp_map = Mat::zeros(good_matches.size(), 4, CV_32F);
    int idx = 0;
    for (vector<DMatch>::iterator it = good_matches.begin(); it != good_matches.end(); it++) {
        Point2f ptA = keypointsA[it->queryIdx].pt;
        Point2f ptB = keypointsB[it->trainIdx].pt;
        warp_map.at<float>(idx, 0) = ptA.x * (1 << OCTAVES);
        warp_map.at<float>(idx, 1) = ptA.y * (1 << OCTAVES);
        warp_map.at<float>(idx, 2) = ptB.x * (1 << OCTAVES);
        warp_map.at<float>(idx, 3) = ptB.y * (1 << OCTAVES);
        idx++;
    }
    write_hdf5_image(out_hdf5, "match_points", warp_map);
    
    // create map by diffusion
    Mat xmap = Mat::zeros(imgA.size(), CV_32F);
    Mat ymap = Mat::zeros(imgA.size(), CV_32F);
    Mat weight = Mat::zeros(imgA.size(), CV_32F);
    for (vector<DMatch>::iterator it = good_matches.begin(); it != good_matches.end(); it++) {
        // map points in A onto B
        Point2f delta = keypointsB[it->trainIdx].pt - keypointsA[it->queryIdx].pt;
        xmap.at<float>(keypointsA[it->queryIdx].pt) = delta.x;
        ymap.at<float>(keypointsA[it->queryIdx].pt) = delta.y;
        weight.at<float>(keypointsA[it->queryIdx].pt) = 1.0;
    }
    for (int iter = 0; iter < 10; iter++) {
        GaussianBlur(xmap, xmap, Size(0, 0), 10.0);
        GaussianBlur(ymap, ymap, Size(0, 0), 10.0);
        GaussianBlur(weight, weight, Size(0, 0), 10.0);
    }
    add(weight, weight == 0, weight, noArray(), CV_32F);
    xmap = xmap / weight;
    ymap = ymap / weight;
    
    // shift and put into 0-1
    for (int xbase = 0; xbase < imgA.cols; xbase++) {
        for (int ybase = 0; ybase < imgA.rows; ybase++) {
            xmap.at<float>(ybase, xbase) += xbase;
            xmap.at<float>(ybase, xbase) /= imgA.cols;
            ymap.at<float>(ybase, xbase) += ybase;
            ymap.at<float>(ybase, xbase) /= imgA.rows;
        }
    }
    //     Mat warpedB;
    //     remap(imgB, warpedB, xmap, ymap, INTER_LINEAR);
    write_hdf5_image(out_hdf5, "column_map", xmap);
    write_hdf5_image(out_hdf5, "row_map", ymap);
    out_hdf5.close();
}
